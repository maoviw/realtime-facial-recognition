# Cognitive Face Live — Reconnaissance faciale temps réel

Tableau de bord de reconnaissance faciale **1:N** en temps réel : flux webcam,
détection de visages et analyse de posture (head-pose) via **Azure Face API**,
vérification biométrique locale via **DeepFace**, historique des reconnaissances
et gestion des visages de référence.

| Couche | Stack |
|--------|-------|
| Backend | Python · FastAPI · DeepFace · Azure Face API · SQLite |
| Frontend | Next.js 16 · React 19 · TypeScript · Tailwind CSS 4 · react-webcam |

---

## Architecture

```
Webcam ──► Frontend (capture périodique, anti-empilement)
              │  POST /analyze-face (base64)
              ▼
          Backend FastAPI
              ├─ Azure Face Detect ──► visages + head-pose (pitch/yaw/roll)
              ├─ DeepFace.verify contre chaque référence (sans filtre de posture)
              ├─ action configurable (log / commande / webhook)
              └─ persistance SQLite (références + historique)
```

### Fonctionnalités

- **Multi-visages** : tous les visages détectés sont analysés (plus seulement le premier).
- **Multi-références** : enrôlez plusieurs personnes ; la meilleure correspondance gagne.
- **Action configurable et cross-platform** : `log`, `command` ou `webhook`
  (remplace l'ancien `notepad.exe` Windows-only).
- **Historique persistant** : chaque événement est journalisé en base SQLite.
- **Enrôlement par upload** : ajout/suppression de visages depuis l'interface.
- **Réglages live** : intervalle de capture, pause/reprise.
- **Sécurité** : CORS restreint, clé API optionnelle, fichiers temporaires uniques.
- **Robustesse** : timeouts HTTP, fallback, logs structurés.

---

## Démarrage rapide (Docker)

```bash
cp backend/.env.example backend/.env   # renseignez vos clés Azure
docker compose up --build
```

- Frontend : http://localhost:3000
- Backend : http://localhost:8000 (docs interactives : http://localhost:8000/docs)

---

## Développement local

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # renseignez AZURE_FACE_ENDPOINT et AZURE_FACE_KEY
uvicorn main:app --reload
```

### Frontend

```bash
cd frontend
npm install
cp .env.example .env.local  # ajustez NEXT_PUBLIC_BACKEND_URL si besoin
npm run dev
```

---

## Tester la reconnaissance sur ce poste Windows

Dans VS Code, lancer les tâches **Reconnaissance locale (127.0.0.1:8000)**
et **Video locale (127.0.0.1:3100)**. Elles sont déjà démarrées à l'issue de
la préparation du 12 septembre 2026. Ouvrir http://127.0.0.1:3100/ dans un
navigateur standard et autoriser la webcam. Les enregistrements sont accessibles
séparément sur http://127.0.0.1:3100/video.

Sans les tâches VS Code, dans deux terminaux PowerShell distincts :

```powershell
# Backend, depuis la racine du projet
Set-Location backend
$env:CORS_ORIGINS = 'http://127.0.0.1:3100,http://localhost:3000'
& ./.venv/Scripts/python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

```powershell
# Frontend, depuis la racine du projet
npm --prefix frontend run dev:video
```

Le fichier backend `.env` existant contient déjà la configuration Azure de ce
poste : ne pas le remplacer. Trois références existantes ont été conservées.
Le runtime DeepFace est installé dans `backend/.venv`, pas dans le `.venv` racine.

1. Vérifier **Backend en ligne**. L'analyse démarre en pause et aucune image
  n'est envoyée automatiquement à l'ouverture.
2. Avec une personne consentante devant la caméra, cliquer **Analyser une image**.
  Consulter la télémétrie et l'historique. Une image sans visage affiche
  **Aucun visage détecté**.
3. Une personne inconnue peut être **auto-enrôlée** par le comportement existant :
  cette première capture crée une référence et un événement, mais renvoie
  `recognized=false` et un score de zéro. Ce n'est pas une identité vérifiée.
  Un second essai teste la comparaison avec les références, sans garantir un succès.
4. Activer **Analyse continue** pour plusieurs captures, puis **Mettre en pause**.
  Une erreur d'analyse arrête la boucle. Une panne Azure renvoie 502 ; un moteur
  absent ou une comparaison incomplète renvoie 503, sans auto-enrôlement de ce visage.
  Si une autre analyse travaille encore, le serveur renvoie 429 avec
  `Retry-After: 1`. Attendre sa fin avant de relancer, même après un délai client dépassé.
5. Pour la D-Link, sélectionner la caméra IP dans **Réglages**, utiliser
  `http://192.168.1.43/image/jpeg.cgi` et saisir les identifiants uniquement dans
  l'interface locale. Le réglage existant les conserve en `localStorage` : profil
  navigateur privé de confiance uniquement. HTTP Basic n'est pas chiffré.

Les captures d'analyse sont envoyées à **Azure Face** ; la comparaison DeepFace
est locale, sur **CPU** dans cet environnement Windows. La première utilisation
de modèles supplémentaires peut demander un téléchargement et dépasser le délai
frontend de 15 secondes. Le score n'est pas une probabilité d'identité calibrée,
et aucune détection de présence réelle (« liveness ») n'est fournie. Garder
`RECOGNITION_ACTION=log` pendant les essais.

Vérifications réalisées : 33 tests API isolés, build et lint frontend (un
avertissement préexistant sur l'image IP), appel Azure réel sur image synthétique
sans visage, inférence VGG-Face sur deux tableaux synthétiques identiques,
essai unique et arrêt sur erreur dans le navigateur, affichage mobile et bureau.
Un banc de charge a également réussi 40 analyses synthétiques via la vraie route
avec DeepFace et son détecteur OpenCV, sans Azure ni analyses démographiques.
Ces contrôles ne mesurent pas la précision sur de vrais visages. Les analyses
démographiques préexistantes n'ont pas été validées sur des personnes.

Ce palier est prêt pour des **essais locaux supervisés**, pas pour une mise en
production. L'enregistrement permanent navigateur fermé, les zones/alertes,
la recherche et les résumés IA du plan global ne sont pas encore livrés.

### Charge IA locale

L'analyse bloquante est exécutée dans le pool de threads FastAPI : elle ne bloque
plus la boucle API. Une seule analyse peut travailler à la fois **par processus**,
y compris après une déconnexion du client. Garder **un seul worker Uvicorn** ;
ce verrou n'est pas une coordination entre plusieurs serveurs.

La dépendance `opencv-python<5` est nécessaire avec le détecteur OpenCV de
DeepFace 0.0.100 : le paquet OpenCV 5.0.0.93 testé ne fournit plus les cascades
Haar attendues. Le parcours réel a été validé avec OpenCV **4.14.0.94**.

Depuis la racine, avec le runtime backend et les dépendances de test installés :

```powershell
& ./backend/.venv/Scripts/python.exe -m scripts.benchmark_recognition --iterations 20 --output ./backend/data/phase0-pilots/recognition-load-local.json
```

Le rapport doit être un nouveau fichier dans un dossier existant. Le script
utilise une base et des images synthétiques temporaires, 1 puis 3 références,
sans auto-enrôlement ni action externe. Il ne contacte pas Azure et ne mesure pas
les modèles démographiques. Les requêtes passent par ASGI dans le même processus,
pas par le réseau du serveur actif. Les poids VGG-Face doivent être disponibles ;
DeepFace peut les télécharger lors d'un premier usage.

Pour une charge soutenue de cinq minutes par galerie, remplacer `--iterations 20`
par `--seconds-per-gallery 300` et choisir un autre fichier de rapport.
Les deux options sont exclusives ; la durée accepte 1 à 900 secondes par galerie.
Le script termine la requête en cours à l'échéance : ce n'est pas un arrêt forcé
d'une inférence bloquée. Les imports s'ajoutent à la durée totale. Chaque requête
consigne sa latence, le temps écoulé et la mémoire résidente du processus (RSS).
Onze tests isolés couvrent les modes, l'échéance, la protection des rapports et
l'arrêt du producteur vidéo après une erreur d'analyse ou d'encodage. Un douzième
test vérifie un encodage HLS réel de quatre secondes (ignoré sans FFmpeg/FFprobe) :

```powershell
& ./backend/.venv/Scripts/python.exe -m pytest scripts/tests/test_benchmark_recognition.py -q
```

Mesure du 12 septembre 2026 : **40/40 réponses 200**, 72,516 s imports compris.
Médianes à chaud : **0,665 s** avec 1 référence et **2,091 s** avec 3 références.
Première requête : 3,983 s, après 13,161 s d'imports. Pic RSS observé : **2,49 Gio**,
CPU moyen équivalent à 2,89 cœurs, aucun GPU TensorFlow. Les appels `/health`
pendant l'analyse ont pris au maximum **35,3 ms** dans ce banc ASGI.
Voir [les limites et preuves de phase 0](docs/phase-0.md#charge-ia-synthetique-et-concurrence).
Ce n'est ni une mesure de précision faciale, ni une endurance IA de 24 heures.

Essai prolongé du même jour : **951/951 réponses 200**, cinq minutes par galerie,
609,625 s imports compris. Médianes à chaud : **0,416 s** (1 référence) et
**1,211 s** (3 références) ; 95es percentiles : 0,462 s et 1,412 s.
Pic RSS observé : **2,39 Gio**. La hausse des médianes RSS entre la première et la
dernière minute est de 1,34 Mio puis 0,11 Mio, sans preuve de stabilité sur 24 h.
Parmi 4 435 sondes `/health`, le maximum observé est **581 ms** dans le banc ASGI.
Ces mesures CPU synthétiques ne couvrent toujours ni Azure, ni la démographie,
ni une charge vidéo simultanée contrôlée. Les écarts avec l'essai court ne
démontrent pas un gain du code de reconnaissance, inchangé entre ces essais.

Pour mesurer l'encodage vidéo en parallèle de DeepFace :

```powershell
& ./backend/.venv/Scripts/python.exe -m scripts.benchmark_recognition --seconds-per-gallery 60 --with-video --output ./backend/data/phase0-pilots/recognition-video-load-local.json
```

Cette option exige le mode chronométré et FFmpeg/FFprobe. Elle réutilise le pilote
HLS avec une mire mobile synthétique 1280×720 à 15 images/s, encodée en H.264
`libx264`. Les clips et la base sont temporaires ; seul le rapport demandé est
conservé. Le rapport vérifie le chevauchement des traitements, la publication
progressive, le nombre d'images, la finalisation et le décodage complet.

Essai du 12 septembre : **203/203 réponses 200**, deux clips de 60 s et 900 images,
environ 60 s de chevauchement par galerie. Médianes à chaud : **0,393 s** et
**1,118 s** (1 puis 3 références), pic RSS global observé **2,40 Gio**,
maximum des 936 sondes de santé **18,4 ms**. Les ressources globales incluent les
processus enfants observés ; le RSS par requête reste celui du processus Python.
C'est une mesure de concurrence CPU avec une mire simple, pas une comparaison
contrôlée des performances ni la chaîne caméra → Azure → reconnaissance : les
images analysées restent les images synthétiques du banc, distinctes de la vidéo.

## Configuration backend (`.env`)

| Variable | Description | Défaut |
|----------|-------------|--------|
| `AZURE_FACE_ENDPOINT` | Endpoint Azure Face | — |
| `AZURE_FACE_KEY` | Clé Azure Face | — |
| `HEADPOSE_PITCH_MAX` / `HEADPOSE_YAW_MAX` | Seuils historiques (filtrage de posture désactivé) | 15 |
| `DEEPFACE_MODEL` | Modèle DeepFace | VGG-Face |
| `RECOGNITION_ACTION` | `none` / `log` / `command` / `webhook` | log |
| `RECOGNITION_COMMAND` | Commande shell (`{name}`, `{confidence}`) | — |
| `RECOGNITION_WEBHOOK_URL` | URL POST appelée à la reconnaissance | — |
| `API_KEY` | Clé API (vide = auth désactivée) | — |
| `CORS_ORIGINS` | Origines autorisées (séparées par virgules) | http://localhost:3000 |

---

## API

| Méthode | Route | Description |
|---------|-------|-------------|
| `GET` | `/health` | État du service |
| `GET` | `/settings` | Seuils & config courante |
| `POST` | `/analyze-face` | Analyse une image (multi-visages) |
| `POST` | `/references` | Enrôle un visage (`name` + `file`) |
| `GET` | `/references` | Liste les références |
| `DELETE` | `/references/{id}` | Supprime une référence |
| `GET` | `/history?limit=N` | Historique des reconnaissances |

Si `API_KEY` est défini, passez l'en-tête `x-api-key` sur les routes protégées.

---

## Tests

Les tests API utilisent une base et des fichiers temporaires par test, sans
charger DeepFace ni appeler Azure. L'environnement de test ne suffit pas à
lancer le backend de reconnaissance réel.

Depuis la racine, dans un environnement Python 3.11 ou 3.12 dédié :

```bash
python -m pip install -r backend/requirements-test.txt
python -m pytest -c backend/pytest.ini --rootdir backend backend/tests -q
python -m pytest scripts/tests -q
npm --prefix frontend run test:video
npm --prefix frontend run lint
npm --prefix frontend run build
```

Sous Windows, pour utiliser l'environnement de test créé dans ce projet :

```powershell
& ./backend/.venv/Scripts/python.exe -m pytest -c backend/pytest.ini --rootdir backend backend/tests -q
```

L'intégration continue ([workflow](.github/workflows/ci.yml)) est configurée
pour Python 3.11/3.12 et les vérifications frontend sur chaque push et pull request.
Les audits de dépendances sont encore non bloquants.

## Phase 0 : état et pilotes vidéo

Le [rapport de phase 0](docs/phase-0.md) détaille les contrôles effectués,
l'environnement Windows et les validations restantes. Le pilote USB publie
progressivement un HLS local, lisible pendant la capture dans `/video`, puis
vérifie sa finalisation et son décodage. Fermer les applications et pages de
capture utilisant la webcam avant son lancement ; le lecteur `/video` peut
rester ouvert, car il n'accède pas directement à la webcam. Ce script n'active
pas l'enregistrement permanent dans l'application.

```powershell
& ./scripts/test-usb-capture.ps1 -DurationSeconds 20
& ./scripts/test-usb-capture.ps1 -Encoder h264_nvenc
```

Le [pilote D-Link](scripts/ip_camera_pilot.py) transmet progressivement les JPEG
complets et validés à FFmpeg pendant la capture. Il publie un HLS H.264/fMP4
lisible avant la fin, puis le finalise et vérifie son décodage intégral.
Il utilise FFmpeg/FFprobe et les dépendances de l'environnement de test backend.

```powershell
& ./backend/.venv/Scripts/python.exe -B scripts/ip_camera_pilot.py --url http://192.168.1.43/video/mjpg.cgi --seconds 10
& ./backend/.venv/Scripts/python.exe -B -m pytest scripts/tests/test_ip_camera_pilot.py -q
```

Le mot de passe est demandé en saisie masquée dans le terminal ; ne pas le
mettre dans la commande ni dans l'URL. HTTP Basic reste non chiffré : réseau
local de confiance uniquement. Les vidéos restent sous `backend/data/phase0-pilots`,
sans appel à Azure/DeepFace. Aucun serveur média n'est lancé par ce script.

### Supervision bornée et stockage

Le [superviseur](scripts/pilot_supervisor.py) enchaîne les pilotes USB, D-Link
ou rejeu local, avec délai maximal, reprises limitées et rapport local.
Depuis la racine, avec l'environnement de test backend et FFmpeg installés :

```powershell
& ./backend/.venv/Scripts/python.exe -m scripts.pilot_supervisor --usb --seconds 20 --cycles 3
& ./backend/.venv/Scripts/python.exe -m scripts.pilot_supervisor --url http://192.168.1.43/video/mjpg.cgi --seconds 20 --cycles 3
& ./backend/.venv/Scripts/python.exe -m scripts.pilot_supervisor --usb --seconds 60 --cycles 10 --retries 1 --metrics
& ./backend/.venv/Scripts/python.exe -m pytest scripts/tests -q
```

Pour D-Link, le mot de passe est demandé une seule fois, masqué dans le terminal.
Il reste en mémoire et passe aux sous-processus par un tube privé, jamais dans
les arguments, variables d'environnement ou rapports. Les erreurs HTTP 401/403
arrêtent les tentatives ; une panne transitoire peut déclencher une reprise.
Le mode USB nécessite Windows et PowerShell 7. Le rejeu utilise `--replay`
et `--content-type` comme le pilote D-Link, avec une cadence de 15 images/s.

L'option `--metrics` nécessite `psutil`, inclus dans les dépendances de test.
Elle ajoute au rapport le pic de mémoire résidente et le temps CPU observés
dans l'arbre de capture. Le pourcentage CPU représente la moyenne par rapport
à un cœur logique, pas la machine entière. L'échantillonnage peut manquer des
processus courts ; la somme des mémoires peut compter des pages partagées
plusieurs fois. Ni le GPU ni la reconnaissance IA ne sont mesurés.

Par défaut : plafond de dossier 512 Mio (`--quota-mib`), réserve disque 2048 Mio
(`--reserve-mib`), marge d'arrêt de 32 Mio et contrôle environ chaque seconde,
plus le temps des contrôles et mesures. Le dossier
entier compte, y compris les anciennes captures et journaux. Il n'y a aucune
purge : si les seuils sont atteints, la supervision s'arrête. Ces contrôles
ne constituent pas un quota garanti par le système de fichiers ; un autre
programme peut consommer le disque entre deux mesures.

Un verrou exclusif empêche deux superviseurs de partager le même dossier.
Les scripts lancés seuls ne participent pas à ce verrou : ne pas les exécuter
en parallèle avec le superviseur. Un verrou laissé après un crash demande une
vérification manuelle des processus avant suppression, jamais un déverrouillage
automatique. Les rapports et journaux sont sous
`backend/data/phase0-pilots/supervision/<identifiant>/` et ne sont pas servis
par l'API vidéo. Ils doivent rester locaux.

Deux limites de session sont disponibles : `--cycles` (1 à 100, défaut 3) ou
`--run-seconds` (1 à 86400), mutuellement exclusives. Les clips restent bornés
de 4 à 60 secondes avec 0 à 3 reprises. Le mode durée peut dépasser 100 cycles.
La durée inclut capture, validation et reprises ; aucun nouvel essai ne démarre
après l'échéance, mais l'essai en cours termine sa validation, sous son délai
maximal habituel. La session peut donc dépasser la durée demandée d'une tentative.

Pour une acquisition autonome de 30 minutes, sans dépendance au navigateur :

```powershell
& ./backend/.venv/Scripts/python.exe -m scripts.pilot_supervisor --url http://192.168.1.43/video/mjpg.cgi --seconds 60 --run-seconds 1800 --retries 1 --metrics --quota-mib 1024 --reserve-mib 4096 --stop-file ./backend/data/phase0-pilots/capture.stop
```

Pour demander un arrêt après l'essai en cours, depuis un second terminal :

```powershell
New-Item -ItemType File -Path ./backend/data/phase0-pilots/capture.stop
```

Le fichier d'arrêt doit être absent au lancement et son dossier doit exister.
Il est conservé après l'arrêt ; choisir un nouveau chemin pour une nouvelle
session ou le retirer manuellement après vérification. Le rapport distingue
`stop_file`, `duration_reached` et `cycles_completed`. Une fin normale de session
ne garantit pas la réussite de toutes les tentatives : consulter aussi
`completed_cycles` et `attempts`. Un problème de stockage ou d'authentification
reste un échec, pas un arrêt normal.

#### Démarrage automatique Windows

PowerShell 7 peut préparer une tâche pour l'utilisateur courant. Le mot de passe
est chiffré par DPAPI, le dossier d'état est limité à cet utilisateur et `SYSTEM`,
et le secret est transmis au superviseur uniquement par stdin. Valider d'abord
la configuration sans installer de tâche :

```powershell
$cameraPassword = Read-Host 'Mot de passe caméra' -AsSecureString
& ./scripts/install-camera-task.ps1 -Url http://192.168.1.43/video/mjpg.cgi -CameraPassword $cameraPassword -PrepareOnly
& ./scripts/run-camera-task.ps1 -ConfigPath "$env:LOCALAPPDATA/CognitiveFaceLive/camera-task.json" -ValidateOnly
```

Après validation, installer et démarrer la tâche :

```powershell
& ./scripts/install-camera-task.ps1 -Url http://192.168.1.43/video/mjpg.cgi -CameraPassword $cameraPassword -StartNow
$cameraPassword = $null
```

Le runner renouvelle les sessions bornées, attend 15 secondes entre elles et
reste actif après une panne de session. État et journal rotatif :
`%LOCALAPPDATA%/CognitiveFaceLive/camera-task-status.json` et
`camera-task.log`. Les quotas arrêtent toujours la capture sans supprimer de
vidéo. Pour arrêter après le clip en cours, puis désinstaller :

```powershell
& ./scripts/stop-camera-task.ps1
& ./scripts/status-camera-task.ps1
& ./scripts/uninstall-camera-task.ps1
# Ajouter -RemoveState uniquement pour supprimer aussi config, statut et secret chiffré.
```

La tâche utilise un déclencheur `AtLogOn` interactif : elle redémarre après une
nouvelle ouverture de session, mais ne capture pas avant connexion Windows et
ne constitue pas un service système. `-StartNow` évite d'attendre la prochaine
connexion lors de l'installation. Windows doit rester éveillé. Les scénarios de
préparation, reprise après échec, arrêt, secret DPAPI corrompu et chemin d'arrêt
non autorisé sont testés. Le 12 septembre 2026, la tâche réelle a été installée
et démarrée avec succès après validation de l'ACL `%LOCALAPPDATA%` et du canal
stdin Windows CRLF. Trois clips autonomes consécutifs de 60 secondes, 320x240,
H.264 et 30 segments ont été finalisés et décodés. Le redémarrage Windows et
une endurance 24 h ne sont pas encore validés.

Le mode direct exige un terminal ouvert ; la tâche planifiée n'en exige pas.
Dans les deux cas, Windows doit rester éveillé et fermer le navigateur n'arrête
pas la capture. `--run-seconds 86400` permet de préparer un essai de
24 heures, mais cet essai **n'a pas été exécuté**. À 2 Mbit/s, prévoir environ
21,6 Go de vidéo par jour, plus journaux, fichiers existants et réserve disque.
Les quotas restent appliqués et peuvent arrêter la session plus tôt ; aucune
augmentation automatique ni suppression de captures n'est effectuée.

Un délai dépassé ou Ctrl+C arrête les sous-processus ; le clip interrompu peut
rester incomplet et n'est pas compté comme valide. Les validations et réouvertures
de caméra créent des pauses entre clips : ce n'est pas un enregistreur continu
ni un service démarrant avec Windows. Dix clips USB d'une minute et trois clips
D-Link réels ont été validés avec mesures CPU/RAM ; cela ne valide pas 24 h
d'endurance. Ces premiers essais D-Link étaient ralentis : environ 31 secondes
d'acquisition donnaient 60 secondes de lecture. Le pilote réseau rééchantillonne
désormais les images selon l'horloge monotone de réception. La correction est
validée sur la D-Link réelle : trois captures de 60,015 à 60,047 secondes
donnent chacune 60 secondes de lecture, sans reprise. Les tests HTTP/FFmpeg
couvrent aussi la coupure et le blocage pendant le flux, suivis de reprises ;
ils ne remplacent pas un débranchement physique de la caméra. Le palier suivant
de 30 captures réelles de 60 secondes a également réussi sans reprise :
30 clips de 60 secondes finalisés et décodables, en 1 869,344 secondes au total.
La lecture pendant une capture D-Link réelle a été observée dans le navigateur.
Voir les [résultats et limites](docs/phase-0.md).

### Lecteur local

Pour consulter les captures USB ou D-Link, pendant ou après leur enregistrement,
dans un autre terminal depuis la racine :

```powershell
npm --prefix frontend run dev:video
```

Ouvrir http://127.0.0.1:3100/video. Ce lancement active explicitement le dossier
vidéo via `HLS_PILOT_ROOT` et lie Next.js à `127.0.0.1` uniquement. Le backend
de reconnaissance n'est pas nécessaire. Si le port est occupé, utiliser
`npm --prefix frontend run dev:video -- --port 3101`.

La page affiche les captures, leur finalisation et un lecteur avec pause,
recherche temporelle et reprise après erreur. `hls.js` est prioritaire, avec
repli HLS natif lorsque MediaSource n'est pas disponible. Sans activation,
l'API vidéo renvoie 404. Seuls les fichiers HLS autorisés sont servis, avec
contrôles de chemins et d'origine. Ces contrôles ne remplacent pas une
authentification : ne pas exposer ce pilote sur le LAN ou via un proxy.

Le mode `--replay <fichier.mjpeg> --content-type <type-multipart>` permet de
réutiliser une capture locale ; `--pace-replay` la transmet à la cadence choisie
pour tester la lecture pendant l'encodage sans accéder à la caméra.

Pour une URL caméra, `--seconds` désigne la durée depuis la première image reçue.
La sortie reste à 15 ou 30 images/s : les images excédentaires sont ignorées et
la dernière image reçue est répétée si la source est plus lente. Le rapport
indique `capture_seconds`, `received_frames` et `timing_mode=monotonic-receive`,
en plus de `playback_seconds`. Le temps de connexion et la validation finale
ne font pas partie de cette durée de capture. Ce ne sont pas les horodatages
du capteur : les délais réseau et d'encodage peuvent affecter ces mesures.
Une fin de flux prématurée reste une erreur. En rejeu, `--seconds` conserve
son sens de durée de lecture à cadence fixe.
Ce script seul ne fournit ni supervision, ni purge ou quota vidéo ; utiliser
le superviseur ci-dessus pour les reprises et seuils de stockage.
La page `/video` n'active aucune analyse faciale ; le parcours de reconnaissance
existant reste séparé et piloté par le navigateur.
