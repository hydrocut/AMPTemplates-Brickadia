# AMPTemplates-Brickadia — Brickadia dedicated server for AMP

**AMP Generic Module** template to host a **Brickadia** dedicated server (multiplayer brick building
game by Brickadia LLC, early access) on Linux. Written for TeamKit, a free French game host, on
10 Oct. 2026. CubeCoders ships no Brickadia template, and no community AMP template was found.

> Résumé en français à la fin du fichier.

**Brickadia is a paid game, its server is not.** The dedicated server (Steam app `3017590`,
*Brickadia Server*, `freetodownload`) downloads **anonymously** through SteamCMD. But it refuses to
start without a **hosting token** created at <https://brickadia.com/account/tokens> by an account
that owns the game. This repository contains no game file and no token.

## What you get

- **One UDP port** (7777 by default). Brickadia reports its port to the server list, so the public
  port must be the same number as the server port (no 7778 → 7777 forwarding).
- **Persistent world**: created on first start, reloaded on every start, saved on every stop.
- **French settings** in AMP: hosting token (masked), password, server name, description, welcome
  message, max players, public listing, world name, scenery of a new world, autosave.
- **Player list in AMP**: joins and leaves, chat lines.
- **AMP console → server console** (`br.Chat.Broadcast …`, `br.World.Save`, `br.Server.Status`…).

## Files

| File | Role |
|---|---|
| `brickadia.kvp`, `brickadia*.json`, `manifest.json` | the AMP template |
| `scripts/lancer-brickadia.py` | launcher, downloaded from this repository at each Update |
| `scripts/AMP_reglages.txt` | template AMP renders into `reglages-amp.txt` at each start |
| `verifier.py` | pre-push checks, must print `Tout est bon.` |

Instance layout: `brickadia/serveur/` = server files (SteamCMD), `brickadia/donnees/` = everything
the game writes (`-UserDir`): `Saved/Config/LinuxServer/GameUserSettings.ini`, `Saved/Worlds/*.brdb`,
`Saved/Server/RoleSetup2.json` (roles), `Saved/Logs/Brickadia.log`.

## Update stages

1. **SteamCMD** app `3017590` (anonymous) → `serveur/` (about 750 MB, build 25104605 on 10 Oct. 2026);
2. **FetchURL** `AMP_reglages.txt` and `lancer-brickadia.py` from this repository (so fixes reach
   existing instances: AMP's *Fetch latest* does not update `App.*` or `Console.*` of an existing
   instance, but every Update re-downloads the launcher);
3. **check** (`lancer-brickadia.py verifier`): binary present, `ldd` finds every library, installed
   Steam build, worlds present.

Container: `cubecoders/ampbase:debian` (Debian 13), **required**. The server binary needs only
glibc and its own `libEOSSDK-Linux-Shipping.so` (`ldd`, checked in a throwaway container on
10 Oct. 2026); `python3` (already in the image) is listed in `ExtraContainerPackages` for the launcher.

## How the server is started

AMP runs `python3 -u lancer-brickadia.py "port=7777" "joueurs=20"`, with the token in the
environment variable `BRICKADIA_JETON` (never on AMP's command line). The launcher:

1. reads `reglages-amp.txt` (rendered by AMP, one raw `key=value` per line, so quotes, commas,
   accents and `#` are safe) and writes **only its own keys** into `GameUserSettings.ini`, keeping
   everything set in game (palettes, limits, rulesets). Texts are written as Unreal quoted strings
   with `\` and `"` escaped; checked against the real server, which reads them back unchanged;
2. runs, from the binary's folder:
   ```
   stdbuf -oL -eL -- BrickadiaServer-Linux-Shipping -log -NotInstalled -UserDir=<instance>/donnees
                     -port=7777 -Token=<token> (-World=<name> | -Environment=<Plate|Peaks|Studio|Space>)
   ```
   (same flags as the omegga wrapper; the game masks the token itself in its log:
   `Command Line: … -Token=*************************`);
3. prints the **ready line** `TeamKit : Brickadia pret sur le port 7777 (monde TeamKit).` once
   authentication is accepted (`Changing AuthState from … to ValidOnline.`) and assets are preloaded;
4. **first start only**: the world file does not exist yet, so the server starts on the chosen
   scenery and the launcher sends `br.World.SaveAs <name> 0`. From then on the world is loaded with
   `-World=<name>` and autosaves go to it (`-World` needs an existing world, as the Pterodactyl egg
   warns);
5. relays AMP's console to the server's standard input;
6. **leaves**: the game logs joins with the name (`LogNet: Join succeeded: <name>`) but not leaves.
   After each player connection is closed (and every 5 min while someone is online) the launcher
   sends `br.Server.Status`, compares the player table with the joins, prints
   `TeamKit : depart de <name>.` and hides its own query from the console. If the names in the table
   do not match the joins (format change), it says so once and stops reporting leaves rather than
   reporting false ones;
7. **stop** (AMP sends SIGTERM): `br.World.Save 0`, wait for `World files saved after …` (60 s max),
   then SIGINT to the server (graceful Unreal exit, as omegga and bricker do), SIGKILL after 25 s.
   The server runs in its own session so only the launcher receives AMP's signal; if the launcher is
   killed outright, the server gets SIGTERM (`PR_SET_PDEATHSIG`) and does not keep the port;
8. if the token is refused (`Authentication failed on dedicated server. Exiting now.` — the game
   then exits with code 0), it explains how to create a token and exits with code 1.

World names are limited to `A-Z a-z 0-9 _ -` (they become a file name and a console argument).

## Settings (French UI)

| Setting | Goes to | Default |
|---|---|---|
| Jeton d'hébergement | `-Token=` (env. variable) | empty: **required** |
| Mot de passe du serveur | `ServerPassword` | empty |
| Nom du serveur | `ServerName` | `Serveur francophone TeamKit.fr` |
| Description | `ServerDescription` | TeamKit + Discord |
| Message d'accueil | `WelcomeMessage` (`{1}` player, `{2}` server, as in the game's default `<color="0055ff">Welcome to <b>{2}</>, {1}.</>`) | French welcome with TeamKit + Discord |
| Joueurs maximum | `MaxPlayers` (and AMP's player count) | 20 |
| Visible dans la liste publique | `bPubliclyListed` | yes |
| Nom du monde | `-World=` / `Saved/Worlds/<name>.brdb` | `TeamKit` |
| Décor d'un nouveau monde | `-Environment=` (first start only) | `Plate` |
| Sauvegarde automatique / intervalle / annonce | `bEnableAutoSave`, `AutoSaveInterval`, `bAnnounceAutoSave` | yes, 5 min, yes |

Sections: `[Server__BP_ServerSettings_General_C BP_ServerSettings_General_C]` and
`[Server__BP_ServerSettingsAutoSave_C BP_ServerSettingsAutoSave_C]` (from omegga and the Pterodactyl egg).
AMP rewrites these keys at every start: a change made in game (*Menu > Edit Game*) to one of them is
replaced at the next start. Other in-game settings are kept.

**Owner and roles:** the account that created the token is the server owner (all permissions) once
authenticated. Roles and role assignments are managed in game and stored in
`donnees/Saved/Server/RoleSetup2.json` and `RoleAssignments.json`; the template does not touch them.

## How players join

- Public server: *Join Game*, find the server name in the list.
- Otherwise: *Join Game > Connect Manually*, type `IP:port`.
- Every player needs their own copy of Brickadia.

## Resources

| | |
|---|---|
| Disk | about 750 MB of server files + worlds (`.brdb`) and backups |
| RAM | **311 MB measured** up to the authentication check (engine initialised, no world loaded); with a world and players: **estimate** 1-2 GB, plan 2 GB (community Linux guide: "at least 2 GB of RAM") |
| CPU | Unreal server ticking at 60 Hz (`max tick rate 60`); the guide suggests 2-4 recent cores |

## What was tested, what was not

Tested on 10 Oct. 2026 in a throwaway `cubecoders/ampbase:debian` container: anonymous SteamCMD
download, `ldd`, the launcher with the **real server** (settings written and read back by the
game, token masked in the log, clear error without a token or with a wrong one), and the full cycle
with a stand-in server (ready, world creation, join, leave, console relay, save and SIGINT on
SIGTERM, server killed with the launcher). **Not tested** (needs a real hosting token): a real
authenticated start, the time to get ready, world creation and loading, real join/leave/chat lines,
`br.Server.Status` output, autosave, and RAM with players.

## Before pushing

```
python verifier.py      # must print "Tout est bon." (the full cycle runs on Linux only)
```

## Sources

- SteamCMD app data (api.steamcmd.net `/v1/info/3017590`): *Brickadia Server*, `freetodownload`,
  Linux depot 3017592, launch `Brickadia/Binaries/Linux/BrickadiaServer-Linux-Shipping -Log`,
  parent app 2199420, branches `public`, `unstable`, `archive-ea1..3`.
- Pterodactyl egg `pterodactyl/game-eggs` `brickadia/egg-brickadia.json` (startup line, token,
  config paths, section names, `br.world.saveas` note).
- brickadia-community/omegga (server wrapper): `src/brickadia/server.ts` (flags, `stdbuf`, SIGINT),
  `src/brickadia/config.ts` (GameUserSettings keys), `src/omegga/matchers/*.ts` (auth, join, leave,
  chat lines), `commandInjector.test.ts` (`br.Server.Status` table), `docs/` (token URL, UDP port).
- notwithering/bricker (Go wrapper): `-Token`, `-World`, `-Environment`, `-UserDir`, stop on SIGINT.
- Brickadia community wiki, *Linux Dedicated Server Setup* (SteamCMD anonymous, UDP 7777, 2 GB RAM,
  `BR.World.SaveAs` before autosave works).
- The server binary itself (strings): `Join succeeded: %ls`, `World files saved after %f.`,
  `Finished preloading primary assets`, `Usage: br.World.SaveAs <path> [<SaveScreenshot = 1>]`,
  default welcome message.

## Credits

Brickadia is © Brickadia LLC. The server EULA forbids redistributing the software: this repository
only contains the template.

---

## Résumé en français

Template AMP pour un **serveur dédié Brickadia** (jeu de construction en briques, accès anticipé).
Le serveur se télécharge **anonymement** par SteamCMD (app 3017590, ~750 Mo), mais il **refuse de
démarrer sans jeton d'hébergement** : à créer sur <https://brickadia.com/account/tokens> avec le
compte qui possède le jeu, à coller dans le champ masqué « Jeton d'hébergement ». Ce compte devient
propriétaire du serveur en jeu.

- **Un seul port, 7777 en UDP**, le même dehors et dedans.
- **Réglages en français** : nom, description et message d'accueil TeamKit par défaut, mot de passe,
  joueurs max (20), liste publique, nom du monde, décor d'un nouveau monde, sauvegarde automatique.
- **Monde** créé au premier démarrage, rechargé ensuite, **sauvegardé à chaque arrêt**.
- **Arrivées, départs et chat** visibles dans AMP ; console d'AMP branchée sur le serveur.
- **Testé** : téléchargement, bibliothèques, lanceur avec le vrai serveur (réglages relus par le jeu,
  jeton masqué, refus clair sans jeton), cycle complet avec un faux serveur. **Pas testé** : un vrai
  démarrage authentifié (il faut un jeton), vraies lignes d'arrivée/départ, mémoire avec des joueurs.
- Avant de pousser : `python verifier.py` doit répondre « Tout est bon. ».
