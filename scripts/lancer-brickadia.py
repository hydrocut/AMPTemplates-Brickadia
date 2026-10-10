#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# lancer-brickadia.py - pose par le template AMP TeamKit (recree a chaque Update).
#
#   python3 lancer-brickadia.py verifier                  etape de verification de l'Update
#   python3 lancer-brickadia.py port=7777 joueurs=20      demarrage (ligne de commande d'AMP)
#
# Ce script se place entre AMP et le serveur Brickadia :
#  1. il lit reglages-amp.txt (ecrit par AMP) et met ces valeurs dans
#     GameUserSettings.ini SANS toucher au reste du fichier (reglages faits en jeu) ;
#  2. il lance le serveur avec le jeton d'hebergement (variable BRICKADIA_JETON),
#     le port et le monde ; si le monde n'existe pas encore, il le cree au premier
#     demarrage (br.World.SaveAs) ;
#  3. il relaie la console d'AMP vers le serveur et ecrit une ligne « pret » ;
#  4. il signale les departs de joueurs (le jeu n'ecrit pas leur nom) ;
#  5. a l'arret demande par AMP (SIGTERM), il sauvegarde le monde puis arrete
#     le serveur proprement (SIGINT).
# Aucune valeur n'est jamais passee par un shell.
import ctypes
import os
import re
import signal
import subprocess
import sys
import threading
import time

ICI = os.path.dirname(os.path.abspath(__file__))
JEU = os.path.join(ICI, 'serveur')
BIN = os.path.join(JEU, 'Brickadia', 'Binaries', 'Linux', 'BrickadiaServer-Linux-Shipping')
MANIFESTE = os.path.join(JEU, 'steamapps', 'appmanifest_3017590.acf')
DONNEES = os.path.join(ICI, 'donnees')            # -UserDir : tout ce que le jeu ecrit
SAVED = os.path.join(DONNEES, 'Saved')
INI = os.path.join(SAVED, 'Config', 'LinuxServer', 'GameUserSettings.ini')
MONDES = os.path.join(SAVED, 'Worlds')
REGLAGES = os.path.join(ICI, 'reglages-amp.txt')  # rendu par AMP depuis AMP_reglages.txt

SEC_GENERAL = '[Server__BP_ServerSettings_General_C BP_ServerSettings_General_C]'
SEC_AUTOSAVE = '[Server__BP_ServerSettingsAutoSave_C BP_ServerSettingsAutoSave_C]'
DECORS = ('Plate', 'Peaks', 'Studio', 'Space')
INTERVALLES = ('5', '10', '15', '30', '60')
NOM_MONDE = re.compile(r'^[A-Za-z0-9_-]{1,40}$')
JETON = re.compile(r'^[\x21-\x7e]{1,200}$')  # pas d'espace, pas de guillemet (controle a part)

# Lignes du jeu (le prefixe [date][compteur] est retire avant).
PREFIXE = re.compile(r'^\[\d{4}\.\d\d\.\d\d-\d\d\.\d\d\.\d\d:\d{3}\]\[ *\d+\]')
AUTH_OK = re.compile(r'^LogBRAuthSubsystem: Changing AuthState from \w+ to Valid(?:Online|Offline)\.$')
AUTH_KO = re.compile(r'^LogBRAuthSubsystem: Error: Authentication failed on dedicated server\.')
PRECHARGE = re.compile(r'^LogBrickadia: Finished preloading primary assets')
SAUVE_OK = re.compile(r'^LogBRWorldManager: World files saved after ')
SAUVE_KO = re.compile(r'^LogBRWorldManager: Error: (?P<msg>.*)$')
ARRIVEE = re.compile(r'^LogNet: Join succeeded: (?P<nom>.+)$')
FERMETURE = re.compile(r'^LogNet: UChannel::Close: .*\bPC: BP_PlayerController')
CRASH = re.compile(r'LogCore: === Critical error: ===|Engine crash handling finished')
STATUT = re.compile(r'^LogConsoleCommands: (?P<t>.*)$')
COMPTE = re.compile(r'^Players \((?P<n>\d+)/(?P<max>\d+)\):')


def dire(texte):
    sys.stdout.write(texte + '\n')
    sys.stdout.flush()


def refuser(texte):
    dire('TeamKit : ERREUR, ' + texte)
    dire("TeamKit : corrige ce reglage dans l'onglet Configuration d'AMP, puis relance.")
    sys.exit(1)


# ── Reglages ─────────────────────────────────────────────────────────────────
def lire_reglages(chemin=REGLAGES):
    """reglages-amp.txt : une ligne cle=valeur, valeur brute ecrite par AMP."""
    v = {}
    with open(chemin, encoding='utf-8', errors='replace') as f:
        for ligne in f:
            ligne = ligne.rstrip('\r\n')
            if not ligne or ligne.startswith('#') or '=' not in ligne:
                continue
            cle, val = ligne.split('=', 1)
            v[cle.strip()] = val
    return v


def texte(val):
    """Texte libre : on retire les caracteres de controle et les espaces au bord."""
    return ''.join(c for c in (val or '') if c >= ' ' and c != '\x7f').strip()


def vrai(val):
    return str(val).strip().lower() in ('true', 'oui', '1', 'yes')


def entre_guillemets(val):
    # Chaine d'un .ini d'Unreal : entre guillemets, \ et " echappes (relu tel quel
    # par le jeu, essai du 10/10/2026 : virgule, point-virgule, #, accents, " ).
    return '"' + val.replace('\\', '\\\\').replace('"', '\\"') + '"'


def valeurs_ini(r, joueurs):
    """Ce que le template impose dans GameUserSettings.ini, section par section."""
    intervalle = r.get('intervalle', '5').strip() or '5'
    if intervalle not in INTERVALLES:
        refuser("intervalle de sauvegarde '%s' : %s minutes." % (intervalle, ', '.join(INTERVALLES)))
    return {
        SEC_GENERAL: [
            ('ServerName', entre_guillemets(texte(r.get('nom')))),
            ('ServerDescription', entre_guillemets(texte(r.get('description')))),
            ('ServerPassword', entre_guillemets(texte(r.get('motdepasse')))),
            ('MaxPlayers', str(joueurs)),
            ('bPubliclyListed', 'True' if vrai(r.get('public')) else 'False'),
            ('WelcomeMessage', entre_guillemets(texte(r.get('accueil')))),
        ],
        SEC_AUTOSAVE: [
            ('bEnableAutoSave', 'True' if vrai(r.get('sauvegarde_auto')) else 'False'),
            ('bAnnounceAutoSave', 'True' if vrai(r.get('annonce')) else 'False'),
            ('AutoSaveInterval', '%d.000000' % (int(intervalle) * 60)),
        ],
    }


def fusionner_ini(contenu, valeurs):
    """Remplace nos cles dans leurs sections, garde tout le reste tel quel
    (palettes, limites et regles reglees en jeu), ajoute ce qui manque."""
    sortie, faites, section = [], set(), None

    def completer(sec):
        if sec in valeurs:
            for cle, val in valeurs[sec]:
                if (sec, cle) not in faites:
                    # avant les lignes vides de fin de section
                    i = len(sortie)
                    while i > 0 and not sortie[i - 1].strip():
                        i -= 1
                    sortie.insert(i, '%s=%s' % (cle, val))
                    faites.add((sec, cle))

    for ligne in contenu.splitlines():
        brut = ligne.strip()
        if brut.startswith('[') and brut.endswith(']'):
            completer(section)
            section = brut
            sortie.append(ligne)
            continue
        if section in valeurs and '=' in brut and not brut.startswith(';'):
            cle = brut.split('=', 1)[0].strip()
            a_nous = dict(valeurs[section])
            if cle in a_nous:
                if (section, cle) not in faites:
                    sortie.append('%s=%s' % (cle, a_nous[cle]))
                    faites.add((section, cle))
                continue  # doublon : supprime
        sortie.append(ligne)
    completer(section)
    for sec, paires in valeurs.items():
        if not any((sec, c) in faites for c, _ in paires):
            if sortie and sortie[-1].strip():
                sortie.append('')
            sortie.append(sec)
            for cle, val in paires:
                sortie.append('%s=%s' % (cle, val))
                faites.add((sec, cle))
    return '\n'.join(sortie) + '\n'


def ecrire_ini(valeurs):
    os.makedirs(os.path.dirname(INI), exist_ok=True)
    contenu = ''
    if os.path.isfile(INI):
        with open(INI, encoding='utf-8', errors='replace') as f:
            contenu = f.read()
    neuf = fusionner_ini(contenu, valeurs)
    tmp = INI + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        f.write(neuf)
    os.replace(tmp, INI)


# ── Verification (etape de l'Update) ────────────────────────────────────────
def verifier():
    if not os.path.isfile(BIN):
        dire('ERREUR : %s absent. Le telechargement SteamCMD (app 3017590) a echoue : relance Update.' % BIN)
        return 1
    os.chmod(BIN, 0o755)
    r = subprocess.run(['ldd', BIN], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       universal_newlines=True, cwd=os.path.dirname(BIN))
    absentes = [l.strip() for l in r.stdout.splitlines() if 'not found' in l]
    if absentes:
        dire('ERREUR : bibliotheques manquantes pour le serveur Brickadia :')
        for l in absentes:
            dire('  ' + l)
        return 1
    dire('Bibliotheques du serveur Brickadia : toutes presentes.')
    if os.path.isfile(MANIFESTE):
        with open(MANIFESTE, encoding='utf-8', errors='replace') as f:
            m = re.search(r'"buildid"\s+"(\d+)"', f.read())
        if m:
            dire('Version Steam installee : build %s.' % m.group(1))
    os.makedirs(MONDES, exist_ok=True)
    mondes = sorted(n[:-5] for n in os.listdir(MONDES) if n.endswith('.brdb'))
    dire('Mondes presents dans donnees/Saved/Worlds : %s.' % (', '.join(mondes) if mondes else 'aucun (cree au premier demarrage)'))
    return 0


# ── Joueurs : le jeu ecrit les arrivees avec le nom, pas les departs ─────────
class Joueurs:
    """Les arrivees viennent de « Join succeeded: <nom> ». Pour les departs, on
    demande br.Server.Status (liste des connectes) et on compare."""

    def __init__(self):
        self.connus = {}          # nom -> heure d'arrivee
        self.suivi = True         # coupe si la liste du jeu ne colle pas aux noms
        self.demande = None       # heure a laquelle interroger le jeu
        self.capture = None       # lignes recues pendant une interrogation
        self.derniere = 0.0

    def arrivee(self, nom, maintenant):
        self.connus[nom] = maintenant
        self.planifier(maintenant + 3)

    def planifier(self, quand):
        if self.suivi and (self.demande is None or quand < self.demande):
            self.demande = quand

    def analyser(self, lignes, maintenant):
        """lignes : textes apres « LogConsoleCommands: ». Renvoie les noms partis."""
        n = None
        for t in lignes:
            m = COMPTE.match(t)
            if m:
                n = int(m.group('n'))
        rangs = [t[1:] for t in lignes if t.startswith('* ')]
        if n is None:
            return None
        if n == 0:
            noms = set()
        else:
            # tableau a colonnes fixes : la largeur du nom vient de l'en-tete
            if not rangs or '|' not in rangs[0] or rangs[0].split('|')[0].strip() != 'Name':
                return None
            largeur = rangs[0].index('|')
            noms = set(r[:largeur].strip() for r in rangs[1:])
        if len(noms) != n:
            return None
        inconnus = [x for x in noms if x not in self.connus]
        if inconnus and self.suivi:
            self.suivi = False
            dire('TeamKit : ATTENTION, les noms de br.Server.Status (%s) ne sont pas ceux des arrivees : '
                 'les departs ne seront pas affiches dans AMP (le jeu a peut-etre change de format).' % ', '.join(sorted(inconnus)))
            return []
        partis = [x for x, t in self.connus.items() if x not in noms and maintenant - t > 10]
        for x in partis:
            del self.connus[x]
        return partis


def corps(ligne):
    return PREFIXE.sub('', ligne, count=1)


# ── Demarrage ───────────────────────────────────────────────────────────────
def demarrer(args):
    v = {}
    for a in args:
        if '=' not in a:
            refuser('argument inattendu : %s' % a)
        cle, val = a.split('=', 1)
        v[cle] = val
    port = v.get('port', '')
    if not port.isdigit() or not 1024 <= int(port) <= 65535:
        refuser("port invalide : '%s'." % port)
    joueurs = v.get('joueurs', '')
    if not joueurs.isdigit() or not 1 <= int(joueurs) <= 100:
        refuser("joueurs maximum '%s' : un nombre entre 1 et 100." % joueurs)
    if not os.path.isfile(REGLAGES):
        refuser("reglages-amp.txt absent : AMP l'ecrit au demarrage a partir de AMP_reglages.txt. Lance Update.")
    r = lire_reglages()
    monde = r.get('monde', '').strip()
    if not NOM_MONDE.match(monde):
        refuser("nom du monde '%s' : 1 a 40 caracteres, lettres sans accent, chiffres, _ ou - (pas d'espace)." % monde)
    decor = r.get('decor', 'Plate').strip() or 'Plate'
    if decor not in DECORS:
        refuser("decor '%s' inconnu (%s)." % (decor, ', '.join(DECORS)))
    jeton = os.environ.get('BRICKADIA_JETON', '').strip()
    if jeton and (not JETON.match(jeton) or '"' in jeton):
        refuser("le jeton d'hebergement contient un espace ou un guillemet : recopie-le depuis brickadia.com/account/tokens.")
    if not os.path.isfile(BIN):
        dire('TeamKit : ERREUR, le serveur Brickadia est absent (%s). Lance Update.' % BIN)
        sys.exit(1)

    ecrire_ini(valeurs_ini(r, int(joueurs)))
    os.makedirs(MONDES, exist_ok=True)
    existe = os.path.isfile(os.path.join(MONDES, monde + '.brdb'))

    cmd = ['stdbuf', '-oL', '-eL', '--', BIN, '-log', '-NotInstalled',
           '-UserDir=' + DONNEES, '-port=' + port]
    if jeton:
        cmd.append('-Token=' + jeton)   # le jeu le masque lui-meme dans son journal (essai du 10/10/2026)
    else:
        dire("TeamKit : ATTENTION, jeton d'hebergement vide. Sans session deja enregistree, Brickadia va refuser de demarrer.")
    if existe:
        cmd.append('-World=' + monde)
        dire('TeamKit : chargement du monde %s (donnees/Saved/Worlds/%s.brdb).' % (monde, monde))
    else:
        cmd.append('-Environment=' + decor)
        dire('TeamKit : le monde %s n existe pas encore, il sera cree sur le decor %s des que le serveur sera pret.' % (monde, decor))
    if os.environ.get('TK_ESSAI_ARGS'):
        # Utilise seulement par verifier.py : ecrit les arguments sans lancer le jeu.
        with open(os.environ['TK_ESSAI_ARGS'], 'w', encoding='utf-8') as f:
            f.write('\n'.join(cmd) + '\n')
        return 0

    def orphelin():
        # Si ce script est tue net, le serveur recoit SIGTERM au lieu de garder le port.
        try:
            ctypes.CDLL('libc.so.6', use_errno=True).prctl(1, signal.SIGTERM)
        except Exception:
            pass

    jeu = subprocess.Popen(cmd, cwd=os.path.dirname(BIN), stdin=subprocess.PIPE,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           start_new_session=True, preexec_fn=orphelin)
    verrou = threading.Lock()
    etat = {'auth': None, 'precharge': False, 'echec_auth': False, 'sauve': None,
            'pret': False, 'monde_actif': existe}
    joueurs_ = Joueurs()

    def ecrire(commande):
        with verrou:
            try:
                jeu.stdin.write((commande + '\n').encode('utf-8'))
                jeu.stdin.flush()
                return True
            except (BrokenPipeError, ValueError, OSError):
                return False

    def lire_jeu():
        for brut in iter(jeu.stdout.readline, b''):
            ligne = brut.decode('utf-8', errors='replace').rstrip('\r\n')
            c = corps(ligne)
            maintenant = time.time()
            if joueurs_.capture is not None:
                m = STATUT.match(c)
                if m:
                    joueurs_.capture.append(m.group('t'))
                    joueurs_.derniere = maintenant
                    continue  # interrogation de TeamKit : pas affichee
            dire(ligne)
            if AUTH_OK.match(c):
                etat['auth'] = etat['auth'] or maintenant
            elif PRECHARGE.match(c):
                etat['precharge'] = True
            elif AUTH_KO.match(c):
                etat['echec_auth'] = True
            elif SAUVE_OK.match(c):
                etat['sauve'] = 'ok'
            elif SAUVE_KO.match(c) and etat['sauve'] == 'attente':
                etat['sauve'] = SAUVE_KO.match(c).group('msg')
            else:
                m = ARRIVEE.match(c)
                if m:
                    joueurs_.arrivee(m.group('nom'), maintenant)
                elif FERMETURE.match(c):
                    joueurs_.planifier(maintenant + 2)
                elif CRASH.search(c):
                    dire('TeamKit : le serveur Brickadia a plante. Le journal complet est dans donnees/Saved/Logs/.')

    def relayer_console():
        for brut in sys.stdin:
            commande = brut.rstrip('\r\n')
            if commande.strip() and jeu.poll() is None:
                if not ecrire(commande):
                    dire('TeamKit : commande non transmise au serveur.')

    threading.Thread(target=lire_jeu, daemon=True).start()
    threading.Thread(target=relayer_console, daemon=True).start()

    arret = {'demande': False}

    def sur_signal(signum, _frame):
        arret['demande'] = True
    signal.signal(signal.SIGTERM, sur_signal)
    signal.signal(signal.SIGINT, sur_signal)

    def attendre_sauvegarde(delai):
        etat['sauve'] = 'attente'
        fin = time.time() + delai
        while time.time() < fin and etat['sauve'] == 'attente' and jeu.poll() is None:
            time.sleep(0.2)
        return etat['sauve']

    prochaine_ronde = 0.0
    while jeu.poll() is None and not arret['demande']:
        time.sleep(0.2)
        maintenant = time.time()
        # Ligne « pret » : authentification acceptee et contenu precharge
        # (ou 20 s apres l'authentification si le precharge ne s'affiche pas).
        if not etat['pret'] and etat['auth'] and (etat['precharge'] or maintenant - etat['auth'] > 20):
            etat['pret'] = True
            dire('TeamKit : Brickadia pret sur le port %s (monde %s).' % (port, monde))
            if not etat['monde_actif']:
                ecrire('br.World.SaveAs %s 0' % monde)
                resultat = attendre_sauvegarde(60)
                if resultat == 'ok':
                    etat['monde_actif'] = True
                    dire('TeamKit : monde %s cree (donnees/Saved/Worlds/%s.brdb), il sera recharge a chaque demarrage.' % (monde, monde))
                else:
                    dire('TeamKit : ATTENTION, le monde %s n a pas pu etre cree (%s). Tape br.World.SaveAs %s dans la console.'
                         % (monde, resultat if resultat != 'attente' else 'pas de reponse', monde))
        # Departs : une interrogation apres chaque fermeture de connexion d'un
        # joueur, et toutes les 5 minutes tant que quelqu'un est connecte.
        if etat['pret'] and joueurs_.suivi and joueurs_.connus and maintenant > prochaine_ronde:
            prochaine_ronde = maintenant + 300
            joueurs_.planifier(maintenant)
        if joueurs_.capture is None and joueurs_.demande and maintenant >= joueurs_.demande and etat['pret']:
            joueurs_.demande = None
            joueurs_.capture, joueurs_.derniere = [], maintenant
            ecrire('br.Server.Status')
        elif joueurs_.capture is not None and maintenant - joueurs_.derniere > 1.5:
            lignes, joueurs_.capture = joueurs_.capture, None
            partis = joueurs_.analyser(lignes, maintenant)
            for nom in partis or []:
                dire('TeamKit : depart de %s.' % nom)

    code = jeu.poll()
    if code is None:
        dire('TeamKit : arret demande.')
        if etat['pret'] and etat['monde_actif']:
            dire('TeamKit : sauvegarde du monde %s avant l arret...' % monde)
            ecrire('br.World.Save 0')
            resultat = attendre_sauvegarde(60)
            dire('TeamKit : monde sauvegarde.' if resultat == 'ok'
                 else 'TeamKit : ATTENTION, sauvegarde non confirmee (%s).' % (resultat if resultat != 'attente' else 'pas de reponse en 60 s'))
        try:
            os.killpg(jeu.pid, signal.SIGINT)
        except OSError:
            pass
        try:
            jeu.wait(25)
        except subprocess.TimeoutExpired:
            dire('TeamKit : le serveur ne s arrete pas, arret force.')
            os.killpg(jeu.pid, signal.SIGKILL)
            jeu.wait()
        return 0
    if etat['echec_auth']:
        dire("TeamKit : ERREUR, Brickadia refuse le jeton d'hebergement (vide, faux ou expire). "
             "Cree un jeton sur https://brickadia.com/account/tokens avec le compte qui possede le jeu, "
             "colle-le dans Configuration > Jeton d'hebergement, puis relance.")
        return 1
    return code


if __name__ == '__main__':
    # Noms de joueurs accentues : toujours de l'UTF-8, quelle que soit la locale du conteneur.
    for flux in (sys.stdout, sys.stdin):
        try:
            flux.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    if sys.argv[1:] == ['verifier']:
        sys.exit(verifier())
    sys.exit(demarrer(sys.argv[1:]))
