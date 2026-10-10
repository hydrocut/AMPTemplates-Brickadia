# -*- coding: utf-8 -*-
"""
Contrôle du template avant de le pousser (même rôle que ceux de Wesnoth, Dominions 6, Freeciv).

    python verifier.py

1. tous les JSON se parsent ;
2. le .kvp ne référence que des fichiers présents, le port principal existe, le bloc
   Console.* / Limits.* est complet, la console d'AMP écrit dans le lanceur, le jeton passe
   par une variable d'environnement et jamais par la ligne de commande ;
3. chaque réglage exposé dans AMP arrive quelque part (modèle scripts/AMP_reglages.txt,
   variable d'environnement ou ligne de commande), et chaque {{...}} a son réglage ;
4. les clés du modèle sont exactement celles que lit lancer-brickadia.py, les choix
   proposés dans AMP sont ceux que le lanceur accepte ;
5. aller-retour : modèle rendu comme AMP (remplacement brut) -> lecture par le lanceur ->
   GameUserSettings.ini -> relecture comme Unreal (chaîne entre guillemets, \\ et \\"
   échappés ; vérifié sur le vrai serveur le 10/10/2026). Valeur vide, espaces, accents,
   virgules, guillemets, antislash, #, ;, balises <color="...">, {1} ;
6. la fusion de GameUserSettings.ini garde les sections et clés réglées en jeu, retire
   les doublons et ne change plus rien au deuxième passage ;
7. les fichiers téléchargés à l'Update existent dans scripts/, à l'URL du dépôt du manifest ;
8. le lanceur : fins de ligne LF, aucun {{, il compile ;
9. les regex de console reconnaissent les vraies lignes du serveur (build Steam 25104605,
   relevées le 10/10/2026 dans un conteneur jetable cubecoders/ampbase:debian, ou chaînes
   lues dans le binaire) et pas les autres ; l'analyse de br.Server.Status ;
10. sous Linux seulement : tout le cycle avec un faux serveur (prêt, création du monde,
   arrivée, départ, console, sauvegarde et arrêt sur SIGTERM).
"""
import importlib.util
import io
import json
import os
import py_compile
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ICI = os.path.dirname(os.path.abspath(__file__))
NOM = 'brickadia'
MODELE = 'scripts/AMP_reglages.txt'
LANCEUR = 'scripts/lancer-brickadia.py'
soucis = []


def lire(nom, binaire=False):
    chemin = os.path.join(ICI, nom)
    if binaire:
        with open(chemin, 'rb') as f:
            return f.read()
    with io.open(chemin, encoding='utf-8') as f:
        return f.read()


def py(motif):
    # AMP est en C# : (?<nom>...) ; Python veut (?P<nom>...)
    return re.compile(motif.replace('(?<', '(?P<'))


spec = importlib.util.spec_from_file_location('lanceur', os.path.join(ICI, LANCEUR))
L = importlib.util.module_from_spec(spec)
spec.loader.exec_module(L)

# ── 1. JSON ──────────────────────────────────────────────────────────────────
data = {}
for nom in ('manifest.json', NOM + 'config.json', NOM + 'metaconfig.json', NOM + 'ports.json', NOM + 'updates.json'):
    try:
        data[nom] = json.loads(lire(nom))
        print('ok   %-26s JSON valide' % nom)
    except Exception as e:
        soucis.append('%s : JSON invalide (%s)' % (nom, e))
if soucis:
    print('\n'.join('NON  ' + s for s in soucis))
    sys.exit(1)
config = data[NOM + 'config.json']
updates = data[NOM + 'updates.json']
ports = data[NOM + 'ports.json']

# ── 2. kvp ───────────────────────────────────────────────────────────────────
kvp = lire(NOM + '.kvp')
cles = dict(l.split('=', 1) for l in kvp.splitlines() if '=' in l)
for ref in re.findall(r'@IncludeJson\[([^\]]+)\]', kvp):
    if not os.path.isfile(os.path.join(ICI, ref)):
        soucis.append('@IncludeJson[%s] : fichier absent' % ref)
for cle in ('Meta.ConfigManifest', 'Meta.MetaConfigManifest'):
    if not os.path.isfile(os.path.join(ICI, cles.get(cle, '?'))):
        soucis.append('%s pointe sur un fichier absent' % cle)
refs = {p['Ref'] for p in ports}
if cles.get('App.PrimaryApplicationPortRef') not in refs:
    soucis.append('PrimaryApplicationPortRef ne correspond à aucun port')
if [p['Protocol'] for p in ports] != ['UDP']:
    soucis.append('Brickadia n’utilise qu’un port, en UDP (IpNetDriver)')
cmd = cles.get('App.CommandLineArgs', '')
for r in re.findall(r'\{\{\$(\w+)\}\}', cmd):
    if r not in refs and r not in ('FullBaseDir', 'MaxUsers'):
        soucis.append('{{$%s}} : aucun port ne porte ce Ref' % r)
if '"port={{$%s}}"' % cles.get('App.PrimaryApplicationPortRef') not in cmd:
    soucis.append('le port principal n’est pas passé au lanceur')
print('ok   ports                      %s' % ', '.join('%s %s/%s' % (p['Ref'], p['Port'], p['Protocol']) for p in ports))
ATTENDUES = ['App.SupportsUniversalSleep', 'App.WakeupMode', 'App.ApplicationReadyMode', 'Console.FilterMatchRegex', 'Console.FilterMatchReplacement', 'Console.ThrowawayMessageRegex', 'Console.AppReadyRegex', 'Console.UserJoinRegex', 'Console.UserLeaveRegex', 'Console.UserChatRegex', 'Console.UpdateAvailableRegex', 'Console.PreConnectRegex', 'Console.ConnectIPRegex', 'Console.MetricsRegex', 'Console.HideFromConsoleRegex', 'Console.SuppressLogAtStart', 'Console.UserActions', 'Limits.SleepMode', 'Limits.SleepOnStart', 'Limits.SleepDelayMinutes', 'Limits.DozeDelay', 'Limits.AutoRetryCount', 'Limits.SleepStartThresholdSeconds']
absentes = [c for c in ATTENDUES if c not in cles]
if absentes:
    soucis.append('clés absentes du .kvp : ' + ', '.join(absentes))
else:
    print('ok   kvp complet                les %d clés Console/Limits sont là' % len(ATTENDUES))
if cles.get('App.ApplicationReadyMode') == 'RegexMatch' and not cles.get('Console.AppReadyRegex'):
    soucis.append('RegexMatch sans AppReadyRegex : l’instance ne serait jamais déclarée prête')
if cles.get('App.HasWriteableConsole') != 'True' or cles.get('App.AdminMethod') != 'STDIO':
    soucis.append('la console d’AMP doit écrire dans le lanceur (HasWriteableConsole=True, AdminMethod=STDIO)')
if cles.get('App.ExitMethod') != 'SIGTERM':
    soucis.append('App.ExitMethod doit être SIGTERM : le lanceur sauvegarde puis arrête le serveur')
if int(cles.get('App.ExitTimeout', '0')) < 90:
    soucis.append('App.ExitTimeout trop court : sauvegarde (60 s) + arrêt (25 s)')
if cles.get('Meta.URL') != data['manifest.json']['url']:
    soucis.append('Meta.URL et manifest.json ne désignent pas le même dépôt')
if cles.get('App.SteamUpdateAnonymousLogin') != 'True':
    soucis.append('le serveur se télécharge en anonyme (App.SteamUpdateAnonymousLogin=True)')
env = json.loads(cles.get('App.EnvironmentVariables', '{}'))
if env.get('BRICKADIA_JETON') != '{{JetonHebergement}}':
    soucis.append('le jeton doit passer par la variable BRICKADIA_JETON')
if 'Jeton' in cmd or 'Token' in cmd:
    soucis.append('le jeton ne doit jamais être sur la ligne de commande d’AMP')
if 'python3' not in json.loads(cles['Meta.ExtraContainerPackages']):
    soucis.append('python3 doit être dans ExtraContainerPackages')

# ── 3. réglages ↔ modèle ─────────────────────────────────────────────────────
modele = lire(MODELE)
dans_modele = re.findall(r'\{\{([A-Za-z0-9_]+)\}\}', modele)
params = {}
for c in config:
    p = c.get('ParamFieldName')
    if p in params:
        soucis.append('réglage en double : %s' % p)
    params[p] = c
    if c['FieldName'] != p and c['FieldName'] != '$MaxUsers':
        soucis.append('%s : FieldName et ParamFieldName différents (%s)' % (p, c['FieldName']))
    if c['FieldName'] == '$MaxUsers':
        if '"joueurs={{$MaxUsers}}"' not in cmd:
            soucis.append('le nombre de joueurs ($MaxUsers) doit être passé au lanceur')
        if cles.get('App.MaxUsers') != c['DefaultValue']:
            soucis.append('App.MaxUsers et la valeur par défaut du réglage diffèrent')
    elif p == 'JetonHebergement':
        if c['InputType'] != 'password' or not c.get('ExcludeFromImport'):
            soucis.append('le jeton doit être un champ masqué (password) exclu des imports')
        if p in dans_modele:
            soucis.append('le jeton ne doit pas être écrit dans reglages-amp.txt')
    elif p not in dans_modele:
        soucis.append('réglage %s exposé dans AMP mais absent de %s' % (p, MODELE))
    if dans_modele.count(p) > 1:
        soucis.append('{{%s}} écrit deux fois dans le modèle' % p)
    if c.get('IncludeInCommandLine') is not False:
        soucis.append('%s : IncludeInCommandLine doit être false' % p)
    if c['InputType'] == 'number' and not str(c.get('DefaultValue', '')).isdigit():
        soucis.append('%s : valeur par défaut non numérique' % p)
    if c['InputType'] == 'checkbox' and c.get('EnumValues') != {'False': 'False', 'True': 'True'}:
        soucis.append('%s : la case doit écrire True / False' % p)
orphelins = set(dans_modele) - set(params)
if orphelins:
    soucis.append('{{...}} sans réglage dans AMP : %s' % ', '.join(sorted(orphelins)))
else:
    print('ok   correspondance             %d réglages : %d dans %s, jeton en variable, joueurs en argument' % (len(params), len(dans_modele), MODELE))
meta = data[NOM + 'metaconfig.json'][0]
racine = cles['App.RootDir'].strip('./').strip('/')
if meta.get('ConfigType') != 'template' or meta.get('ConfigFileTemplate') != racine + '/AMP_reglages.txt':
    soucis.append('metaconfig : ConfigFileTemplate devrait être %s/AMP_reglages.txt' % racine)
if meta.get('ConfigFile') != os.path.basename(L.REGLAGES):
    soucis.append('le fichier rendu par AMP (ConfigFile) n’est pas celui que lit le lanceur')
for p in ('DescriptionServeur', 'MessageAccueil'):
    if 'TeamKit.fr' not in params[p]['DefaultValue'] or 'discord.gg/EuAjGMYPHY' not in params[p]['DefaultValue']:
        soucis.append('%s par défaut doit citer TeamKit.fr et le Discord' % p)
if not L.NOM_MONDE.match(params['Monde']['DefaultValue']):
    soucis.append('le monde par défaut est refusé par le lanceur')

# ── 4. clés du modèle = clés lues par le lanceur, choix = choix acceptés ─────
cle_modele = {}
for l in modele.splitlines():
    if l.startswith('#') or not l.strip():
        if '{{' in l:
            soucis.append('{{ dans un commentaire du modèle : AMP le remplacerait : %s' % l)
        continue
    m = re.match(r'^([a-z_]+)=\{\{([A-Za-z0-9_]+)\}\}$', l)
    if not m:
        soucis.append('ligne du modèle inattendue : %s' % l)
        continue
    cle_modele[m.group(1)] = m.group(2)
source = lire(LANCEUR)
lues = set(re.findall(r"r\.get\('([a-z_]+)'", source))
if lues != set(cle_modele):
    soucis.append('clés du modèle %s et clés lues par le lanceur %s différentes' % (sorted(cle_modele), sorted(lues)))
if set(params['Decor']['EnumValues']) != set(L.DECORS) or params['Decor']['DefaultValue'] not in L.DECORS:
    soucis.append('décors proposés dans AMP et acceptés par le lanceur différents')
if set(params['IntervalleSauvegarde']['EnumValues']) != set(L.INTERVALLES):
    soucis.append('intervalles proposés dans AMP et acceptés par le lanceur différents')
if int(params['MaxPlayers']['MaxValue']) != 100 or int(params['MaxPlayers']['MinValue']) != 1:
    soucis.append('bornes des joueurs : le lanceur accepte 1 à 100')
print('ok   modèle ↔ lanceur           %d clés, décors %s, intervalles %s min' % (len(cle_modele), '/'.join(L.DECORS), '/'.join(L.INTERVALLES)))


# ── 5. aller-retour AMP -> lanceur -> GameUserSettings.ini -> Unreal ─────────
def rendre(valeurs):
    t = modele
    for p, v in valeurs.items():
        t = t.replace('{{%s}}' % p, v)
    return t


def lire_ue(contenu, section, cle):
    """Lecture d'une valeur comme Unreal : entre guillemets, \\x -> x ; sinon brute, rognée."""
    dans = False
    for l in contenu.splitlines():
        s = l.strip()
        if s.startswith('['):
            dans = (s == section)
            continue
        if dans and s.startswith(cle + '='):
            v = s[len(cle) + 1:]
            if v.startswith('"'):
                sortie, i = [], 1
                while i < len(v):
                    if v[i] == '\\' and i + 1 < len(v):
                        sortie.append(v[i + 1])
                        i += 2
                        continue
                    if v[i] == '"':
                        if v[i + 1:].strip():
                            return None  # texte après le guillemet fermant : cassé
                        return ''.join(sortie)
                    sortie.append(v[i])
                    i += 1
                return None  # guillemet non fermé
            return v.strip()
    return None


defauts = {p: str(c.get('DefaultValue', '')) for p, c in params.items() if p in dans_modele}
ESSAIS = [
    '',
    '   avec des espaces   ',
    'Serveur francophone hébergé gratuitement par TeamKit.fr — Discord : discord.gg/EuAjGMYPHY',
    'Dites "bonjour", l\'équipe ; #1 {2} {1}',
    'Bienvenue sur <b>{2}</>, {1} ! <color="0055ff">bleu</>',
    'C:\\chemin\\ et \\" piège "\\',
    'tab\tet\x01contrôle',
]
CHAMPS_TEXTE = {'NomServeur': 'ServerName', 'DescriptionServeur': 'ServerDescription',
                'MessageAccueil': 'WelcomeMessage', 'MotDePasse': 'ServerPassword'}
n = 0
tmp = tempfile.mkdtemp(prefix='tk-brick-')
try:
    chemin = os.path.join(tmp, 'reglages-amp.txt')
    for p, cle_ini in CHAMPS_TEXTE.items():
        for v in ESSAIS:
            vals = dict(defauts)
            vals[p] = v
            with open(chemin, 'w', encoding='utf-8', newline='\n') as f:
                f.write(rendre(vals))
            r = L.lire_reglages(chemin)
            ini = L.fusionner_ini('', L.valeurs_ini(r, 20))
            lu = lire_ue(ini, L.SEC_GENERAL, cle_ini)
            attendu = L.texte(v)
            n += 1
            if lu != attendu:
                soucis.append('aller-retour %s : écrit %r, relu %r' % (p, v, lu))
            if '\t' in v and '\t' in (lu or ''):
                soucis.append('caractère de contrôle passé dans %s' % cle_ini)
    # cases et listes
    for p, cle_ini, sec in [('ListePublique', 'bPubliclyListed', L.SEC_GENERAL),
                            ('SauvegardeAuto', 'bEnableAutoSave', L.SEC_AUTOSAVE),
                            ('AnnonceSauvegarde', 'bAnnounceAutoSave', L.SEC_AUTOSAVE)]:
        for v in ('True', 'False'):
            vals = dict(defauts)
            vals[p] = v
            with open(chemin, 'w', encoding='utf-8', newline='\n') as f:
                f.write(rendre(vals))
            ini = L.fusionner_ini('', L.valeurs_ini(L.lire_reglages(chemin), 20))
            n += 1
            if lire_ue(ini, sec, cle_ini) != v:
                soucis.append('%s=%s mal recopié' % (p, v))
    for v in L.INTERVALLES:
        vals = dict(defauts)
        vals['IntervalleSauvegarde'] = v
        with open(chemin, 'w', encoding='utf-8', newline='\n') as f:
            f.write(rendre(vals))
        ini = L.fusionner_ini('', L.valeurs_ini(L.lire_reglages(chemin), 7))
        n += 1
        if lire_ue(ini, L.SEC_AUTOSAVE, 'AutoSaveInterval') != '%d.000000' % (int(v) * 60) or lire_ue(ini, L.SEC_GENERAL, 'MaxPlayers') != '7':
            soucis.append('intervalle %s ou joueurs mal recopiés' % v)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print('ok   aller-retour               %d valeurs (vide, espaces, accents, ", \\, #, ;, {1}, <color>, contrôle)' % n)

# ── 6. fusion de GameUserSettings.ini ───────────────────────────────────────
# Fichier tel que le vrai serveur l'a réécrit en s'arrêtant (10/10/2026), plus des
# réglages faits en jeu et un doublon.
EN_JEU = '''\
;METADATA=(Diff=true, UseCommands=true)
[Server__BRServerSettings_Color BRServerSettings_Color]
bSavedColorsAreLinear=False

[Server__BP_ServerSettingsAutoSave_C BP_ServerSettingsAutoSave_C]
bEnableAutoSave=False
AutoSaveInterval=120.000000

[Server__BP_ServerSettings_General_C BP_ServerSettings_General_C]
ServerName=Ancien nom
MaxPrefabBricks=5000
bGlobalRulesetSelfDamage=False
ServerName=Doublon
WelcomeMessage="ancien"
'''
vals = L.valeurs_ini({'nom': 'Nouveau', 'public': 'True', 'sauvegarde_auto': 'True', 'intervalle': '15', 'annonce': 'False'}, 30)
une = L.fusionner_ini(EN_JEU, vals)
deux = L.fusionner_ini(une, vals)
if une != deux:
    soucis.append('la fusion change encore le fichier au deuxième passage')
for attendu in ('MaxPrefabBricks=5000', 'bGlobalRulesetSelfDamage=False', '[Server__BRServerSettings_Color BRServerSettings_Color]',
                'bSavedColorsAreLinear=False', ';METADATA=(Diff=true, UseCommands=true)', 'ServerName="Nouveau"',
                'AutoSaveInterval=900.000000', 'bAnnounceAutoSave=False', 'MaxPlayers=30', 'bPubliclyListed=True'):
    if attendu not in une.splitlines():
        soucis.append('fusion : ligne attendue absente : %s' % attendu)
for interdit in ('Ancien nom', 'Doublon', 'bEnableAutoSave=False', 'AutoSaveInterval=120.000000', '"ancien"'):
    if interdit in une:
        soucis.append('fusion : %s aurait dû être remplacé' % interdit)
if une.count('[Server__BP_ServerSettings_General_C BP_ServerSettings_General_C]') != 1:
    soucis.append('fusion : section générale en double')
vide = L.fusionner_ini('', vals)
if vide.count('[') != 2 or lire_ue(vide, L.SEC_GENERAL, 'ServerName') != 'Nouveau':
    soucis.append('fusion d’un fichier vide incorrecte')
print('ok   fusion de l’ini            réglages faits en jeu gardés, doublon retiré, stable au 2e passage')

# ── 7. fichiers téléchargés à l'Update ───────────────────────────────────────
depot = data['manifest.json']['url'].replace('https://github.com/', '')
fetch = [e for e in updates if e['UpdateSource'] == 'FetchURL']
for e in fetch:
    attendu = 'https://raw.githubusercontent.com/%s/main/scripts/%s' % (depot, e['UpdateSourceArgs'])
    if e['UpdateSourceData'] != attendu:
        soucis.append('URL inattendue pour %s : %s' % (e['UpdateSourceArgs'], e['UpdateSourceData']))
    elif not os.path.isfile(os.path.join(ICI, 'scripts', e['UpdateSourceArgs'])):
        soucis.append('scripts/%s absent du dépôt' % e['UpdateSourceArgs'])
    else:
        print('ok   téléchargé à l’Update      scripts/%s' % e['UpdateSourceArgs'])
if {e['UpdateSourceArgs'] for e in fetch} != {'AMP_reglages.txt', 'lancer-brickadia.py'}:
    soucis.append('l’Update doit déposer AMP_reglages.txt et lancer-brickadia.py')
steam = [e for e in updates if e['UpdateSource'] == 'SteamCMD']
if len(steam) != 1 or steam[0]['UpdateSourceData'] != '3017590' or not steam[0].get('UpdateSourceTarget', '').endswith('serveur'):
    soucis.append('étape SteamCMD : app 3017590 dans serveur/')
elif os.path.relpath(L.BIN, L.ICI).replace('\\', '/') != 'serveur/Brickadia/Binaries/Linux/BrickadiaServer-Linux-Shipping':
    soucis.append('le lanceur ne cherche pas le binaire là où SteamCMD le pose')
if updates[-1]['UpdateSource'] != 'Executable' or 'verifier' not in updates[-1]['UpdateSourceArgs']:
    soucis.append('la dernière étape doit être la vérification du lanceur')

# ── 8. lanceur ───────────────────────────────────────────────────────────────
for nom in (LANCEUR, MODELE):
    if b'\r' in lire(nom, binaire=True):
        soucis.append('%s : fins de ligne Windows (CRLF)' % nom)
if '{{' in source:
    soucis.append('le lanceur contient {{ : AMP pourrait le remplacer')
try:
    py_compile.compile(os.path.join(ICI, LANCEUR), doraise=True, cfile=os.path.join(tempfile.gettempdir(), 'tk-brick.pyc'))
    print('ok   compilation                %s' % LANCEUR)
except py_compile.PyCompileError as e:
    soucis.append('le lanceur ne compile pas : %s' % e)
for besoin, pourquoi in [
    ("'-UserDir=' + DONNEES", 'données du jeu dans le dossier de l’instance'),
    ("'-Token=' + jeton", 'jeton passé au jeu'),
    ("'-World=' + monde", 'monde rechargé'),
    ("'br.World.SaveAs %s 0'", 'création du monde'),
    ("'br.World.Save 0'", 'sauvegarde à l’arrêt'),
    ('start_new_session=True', 'le SIGTERM d’AMP ne doit arriver qu’au lanceur'),
    ('signal.SIGINT', 'arrêt propre du serveur'),
    ("'stdbuf', '-oL'", 'sortie du jeu ligne par ligne'),
]:
    if besoin not in source:
        soucis.append('lanceur : %s (%s)' % (besoin, pourquoi))

# ── 9. regex de console sur de vraies lignes ─────────────────────────────────
pret_ligne = 'TeamKit : Brickadia pret sur le port 7777 (monde TeamKit).'
ESSAIS_CONSOLE = [
    ('Console.AppReadyRegex', pret_ligne, {}),
    # « Join succeeded: %ls » (chaîne du binaire), préfixe des lignes relevé le 10/10/2026
    ('Console.UserJoinRegex', '[2026.10.10-17.35.19:480][ 52]LogNet: Join succeeded: Kevin', {'username': 'Kevin'}),
    ('Console.UserJoinRegex', 'LogNet: Join succeeded: Jean Luc é_2', {'username': 'Jean Luc é_2'}),
    ('Console.UserLeaveRegex', 'TeamKit : depart de Jean Luc é_2.', {'username': 'Jean Luc é_2'}),
    # format du chat d'après omegga (src/omegga/matchers/chat.ts)
    ('Console.UserChatRegex', '[2026.10.10-17.40.02:111][812]LogChat: [SenderId: 00000000-0000-4000-8000-000000000001] Kevin: salut: ça va ?', {'username': 'Kevin', 'message': 'salut: ça va ?'}),
    ('Console.ThrowawayMessageRegex', '[2026.10.10-17.35.02:754][  0]LogConfig: Changed CVar p.NetEnableListenServerSmoothing=1', {}),
    ('Console.ThrowawayMessageRegex', 'LogPluginManager: Mounting Engine plugin Paper2D', {}),
]
for cle, ligne, attendu in ESSAIS_CONSOLE:
    m = py(cles.get(cle) or '^$').search(ligne)
    if not m or any(m.group(k) != v for k, v in attendu.items()):
        soucis.append('%s ne reconnaît pas : %r' % (cle, ligne))
PIEGES = [
    ('Console.UserJoinRegex', '[2026.10.10-17.35.19:480][ 52]LogNet: Join request: /Game/Maps/Plate/Plate?Name=Kevin'),
    ('Console.UserJoinRegex', 'TeamKit : depart de Kevin.'),
    ('Console.UserLeaveRegex', '[2026.10.10-17.35.19:480][ 52]LogNet: Join succeeded: Kevin'),
    ('Console.AppReadyRegex', '[2026.10.10-17.45.16:412][  0]LogBRAuthSubsystem: Changing AuthState from Invalid to PendingManualCheckLogin.'),
    ('Console.ThrowawayMessageRegex', '[2026.10.10-17.45.16:689][ 13]LogBRAuthSubsystem: Error: Authentication failed on dedicated server. Exiting now.'),
    ('Console.ThrowawayMessageRegex', 'TeamKit : Brickadia pret sur le port 7777 (monde TeamKit).'),
    ('Console.ThrowawayMessageRegex', '[2026.10.10-17.35.02:929][  0]LogNet: Name:GameNetDriver Def:GameNetDriver IpNetDriver_2147479601 IpNetDriver listening on port 7777'),
    ('Console.UserChatRegex', '[2026.10.10-17.40.02:111][812]LogChat: Someone was banned by Admin (being rude), expires in 1 minute'),
]
for cle, ligne in PIEGES:
    if py(cles[cle]).search(ligne):
        soucis.append('%s attrape une ligne qui n’est pas pour lui : %r' % (cle, ligne))
# lignes réelles reconnues par le lanceur lui-même
for motif, ligne in [
    (L.AUTH_KO, '[2026.10.10-17.45.16:689][ 13]LogBRAuthSubsystem: Error: Authentication failed on dedicated server. Exiting now.'),
    (L.AUTH_OK, '[2026.10.10-17.45.16:689][ 13]LogBRAuthSubsystem: Changing AuthState from PendingManualCheckLogin to ValidOnline.'),
    (L.PRECHARGE, '[2026.10.10-17.45.20:001][ 99]LogBrickadia: Finished preloading primary assets'),
    (L.SAUVE_OK, '[2026.10.10-17.46.00:100][300]LogBRWorldManager: World files saved after 0.231.'),
    (L.ARRIVEE, '[2026.10.10-17.46.00:100][300]LogNet: Join succeeded: Kevin'),
]:
    if not motif.match(L.corps(ligne)):
        soucis.append('le lanceur ne reconnaît pas : %r' % ligne)
if L.AUTH_OK.match(L.corps('[2026.10.10-17.45.16:412][  0]LogBRAuthSubsystem: Changing AuthState from Invalid to PendingManualCheckLogin.')):
    soucis.append('AUTH_OK attrape PendingManualCheckLogin')
# br.Server.Status : format d'omegga (src/omegga/commandInjector.test.ts)
L.dire = lambda t: None  # l'avertissement du lanceur n'a rien à faire ici
j = L.Joueurs()
j.connus = {'TestPlayer': 0.0, 'Parti': 0.0}
STATUT_UN = ['Server Name: Test Server', 'Description: a description', 'Bricks: 62575', 'Components: 44993', 'Time: 1m 47s', 'Players (1/50):',
             '* Name       |  Ping |   Time | Roles                                      | Address  | ID                                  ',
             '* TestPlayer | 182ms | 0m 47s | Admin, Moderator, Builder, Trusted, Member | 12.3.4.5 | 00000000-0000-4000-8000-000000000001']
if j.analyser(STATUT_UN, 100.0) != ['Parti'] or set(j.connus) != {'TestPlayer'}:
    soucis.append('br.Server.Status : départ mal détecté')
if j.analyser(['Server Name: x', 'Time: 0m 1s', 'Players (0/50):', '* Name | Ping | Time | Roles | Address | ID'], 100.0) != ['TestPlayer']:
    soucis.append('br.Server.Status : serveur vide mal lu')
j.connus = {'Récent': 95.0}
if j.analyser(['Players (0/50):'], 100.0) != []:
    soucis.append('un joueur arrivé il y a moins de 10 s ne doit pas être déclaré parti')
j.connus = {'Kevin': 0.0}
if j.analyser(['Players (2/50):', '* Name | Ping', '* Kevin | 1ms'], 100.0) is not None:
    soucis.append('un tableau incomplet (2 annoncés, 1 lu) doit être ignoré')
j2 = L.Joueurs()
j2.connus = {'Kevin': 0.0}
if j2.analyser(['Players (1/50):', '* Name  | Ping', '* Autre | 1ms'], 100.0) != [] or j2.suivi:
    soucis.append('des noms inconnus doivent couper le suivi des départs (pas de faux départs)')
print('ok   console                    %d lignes, %d pièges, br.Server.Status' % (len(ESSAIS_CONSOLE), len(PIEGES)))

# ── 10. cycle complet avec un faux serveur (Linux) ───────────────────────────
FAUX = r'''#!/usr/bin/env python3
import os, signal, sys, threading, time
args = sys.argv[1:]
ud = [a.split('=', 1)[1] for a in args if a.startswith('-UserDir=')][0]
print('LogInit: Command Line:  ' + ' '.join('-Token=***' if a.startswith('-Token=') else a for a in args), flush=True)
joueurs = []
def p(t):
    print('[2026.10.10-17.00.00:000][  1]' + t, flush=True)
def fin(*_):
    p('LogExit: Game engine shut down')
    open(os.path.join(ud, 'arret.txt'), 'w').write('SIGINT')
    sys.exit(0)
signal.signal(signal.SIGINT, fin)
signal.signal(signal.SIGTERM, lambda *_: (open(os.path.join(ud, 'arret.txt'), 'w').write('SIGTERM'), sys.exit(3)))
if not any(a.startswith('-Token=') for a in args):
    p('LogBRAuthSubsystem: Error: Authentication failed on dedicated server. Exiting now.')
    sys.exit(0)
p('LogBRAuthSubsystem: Changing AuthState from PendingManualCheckLogin to ValidOnline.')
p('LogBrickadia: Finished preloading primary assets')
def scenario():
    time.sleep(1.0)
    joueurs.append('Kévin T')
    p('LogNet: Join succeeded: Kévin T')
    time.sleep(12.5)
    joueurs.remove('Kévin T')
    p('LogNet: UChannel::Close: Sending CloseBunch. ChIndex == 0. Name: [UChannel] ChIndex: 0, Closing: 0 [UNetConnection] RemoteAddr: 1.2.3.4:5, Name: IpConnection_1, Driver: GameNetDriver, IsServer: YES, PC: BP_PlayerController_C_1, Owner: BP_PlayerController_C_1, UniqueId: X')
threading.Thread(target=scenario, daemon=True).start()
for l in sys.stdin:
    l = l.strip()
    with open(os.path.join(ud, 'commandes.txt'), 'a') as f:
        f.write(l + '\n')
    if l.startswith('br.World.SaveAs '):
        nom = l.split()[1]
        os.makedirs(os.path.join(ud, 'Saved', 'Worlds'), exist_ok=True)
        open(os.path.join(ud, 'Saved', 'Worlds', nom + '.brdb'), 'w').write('monde')
        p('LogBRWorldManager: World files saved after 0.1.')
    elif l == 'br.World.Save 0':
        p('LogBRWorldManager: World files saved after 0.1.')
    elif l == 'br.Server.Status':
        p('LogConsoleCommands: Server Name: x')
        p('LogConsoleCommands: Time: 0m 1s')
        p('LogConsoleCommands: Players (%d/20):' % len(joueurs))
        p('LogConsoleCommands: * Name    | Ping')
        for j in joueurs:
            p('LogConsoleCommands: * %-7s | 1ms' % j)
    else:
        p('LogConsoleCommands: recu ' + l)
'''
if os.name == 'posix' and shutil.which('stdbuf'):
    base = tempfile.mkdtemp(prefix='tk-brick-essai-')
    try:
        inst = os.path.join(base, 'brickadia')
        binaire = os.path.join(inst, 'serveur', 'Brickadia', 'Binaries', 'Linux', 'BrickadiaServer-Linux-Shipping')
        os.makedirs(os.path.dirname(binaire))
        with open(binaire, 'w') as f:
            f.write(FAUX)
        os.chmod(binaire, 0o755)
        shutil.copy(os.path.join(ICI, LANCEUR), inst)
        with open(os.path.join(inst, 'reglages-amp.txt'), 'w', encoding='utf-8') as f:
            f.write(rendre(defauts))
        envi = dict(os.environ, BRICKADIA_JETON='jeton-essai-123', PYTHONIOENCODING='utf-8')
        proc = subprocess.Popen([sys.executable, '-u', os.path.join(inst, 'lancer-brickadia.py'), 'port=7777', 'joueurs=20'],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=envi)
        time.sleep(4)
        proc.stdin.write('br.Chat.Broadcast Bonjour à tous\n'.encode('utf-8'))
        proc.stdin.flush()
        time.sleep(14)
        proc.send_signal(signal.SIGTERM)
        sortie = proc.communicate(timeout=60)[0].decode('utf-8')
        don = os.path.join(inst, 'donnees')
        commandes = open(os.path.join(don, 'commandes.txt'), encoding='utf-8').read().splitlines()
        lignes = sortie.splitlines()
        for attendu in ['TeamKit : Brickadia pret sur le port 7777 (monde TeamKit).',
                        'TeamKit : monde TeamKit cree (donnees/Saved/Worlds/TeamKit.brdb), il sera recharge a chaque demarrage.',
                        '[2026.10.10-17.00.00:000][  1]LogNet: Join succeeded: Kévin T',
                        'TeamKit : depart de Kévin T.',
                        'TeamKit : monde sauvegarde.']:
            if attendu not in lignes:
                soucis.append('cycle : ligne absente : %s' % attendu)
        if any(py(cles['Console.AppReadyRegex']).match(l) for l in lignes) is False:
            soucis.append('cycle : AppReadyRegex ne voit pas la ligne prêt')
        if not any(py(cles['Console.UserLeaveRegex']).match(l) for l in lignes):
            soucis.append('cycle : UserLeaveRegex ne voit pas le départ')
        if any('LogConsoleCommands: * ' in l for l in lignes):
            soucis.append('cycle : la réponse à br.Server.Status de TeamKit ne doit pas s’afficher')
        if 'jeton-essai-123' in sortie:
            soucis.append('cycle : le jeton apparaît dans la console')
        if commandes[:1] != ['br.World.SaveAs TeamKit 0'] or 'br.Chat.Broadcast Bonjour à tous' not in commandes \
                or 'br.Server.Status' not in commandes or commandes[-1] != 'br.World.Save 0':
            soucis.append('cycle : commandes reçues par le serveur inattendues : %s' % commandes)
        if open(os.path.join(don, 'arret.txt')).read() != 'SIGINT' or proc.returncode != 0:
            soucis.append('cycle : arrêt attendu par SIGINT avec le code 0 (reçu %s)' % proc.returncode)
        ini = open(os.path.join(don, 'Saved', 'Config', 'LinuxServer', 'GameUserSettings.ini'), encoding='utf-8').read()
        if lire_ue(ini, L.SEC_GENERAL, 'ServerName') != defauts['NomServeur']:
            soucis.append('cycle : GameUserSettings.ini mal écrit')
        # 2e démarrage : le monde existe, il est rechargé ; sans jeton : refus clair, code 1
        a = os.path.join(base, 'args.txt')
        subprocess.run([sys.executable, os.path.join(inst, 'lancer-brickadia.py'), 'port=7777', 'joueurs=20'],
                       env=dict(envi, TK_ESSAI_ARGS=a), stdout=subprocess.DEVNULL, check=True)
        if '-World=TeamKit' not in open(a).read().split():
            soucis.append('cycle : le monde créé n’est pas rechargé au démarrage suivant')
        envi.pop('BRICKADIA_JETON')
        r = subprocess.run([sys.executable, os.path.join(inst, 'lancer-brickadia.py'), 'port=7777', 'joueurs=20'],
                           env=envi, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        if r.returncode != 1 or 'brickadia.com/account/tokens' not in r.stdout.decode('utf-8'):
            soucis.append('cycle : sans jeton, le lanceur doit sortir en 1 avec l’explication')
        print('ok   cycle complet              faux serveur : prêt, monde créé, arrivée, départ, console, sauvegarde, SIGINT')
    finally:
        shutil.rmtree(base, ignore_errors=True)
else:
    print('--   cycle complet              pas de Linux/stdbuf ici : testé dans le conteneur (voir FAISABILITE.md)')

print()
if soucis:
    print('%d problème(s) :' % len(soucis))
    print('\n'.join('  NON  ' + s for s in soucis))
    sys.exit(1)
print('Tout est bon.')
