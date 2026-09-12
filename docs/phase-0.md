# Phase 0 : baseline et pilote materiel

Etat au 2026-09-12 : socle logiciel, pilote USB progressif avec lecture pendant
capture, capture JPEG D-Link et pilote D-Link H.264/HLS court verifies.
La lecture progressive D-Link est validee sur rejeu local. La cible IP est la seule DCS-932LB disponible ;
RTSP et le multi-camera sont des extensions futures, pas des prerequis.
La supervision bornee et les seuils de stockage sont maintenant testes, avec
dix clips USB d'une minute et trente clips D-Link reels avec mesures CPU/RAM.
La lecture HLS avant fin de capture est confirmee dans le navigateur sur la
D-Link reelle. Le backend Azure/DeepFace et le tableau de bord sont demarres
pour des essais locaux supervises. La phase 0 reste ouverte : service permanent,
endurance 24 h et budget complet camera/Azure/IA non valides. Les charges
DeepFace synthetiques de dix minutes et video/DeepFace simultanees de deux
minutes sont maintenant mesurees ; elles ne valident pas la chaine complete.
La correction de cadence D-Link est validee sur la camera reelle : trois
captures d'environ 60 secondes donnent chacune 60 secondes de lecture. Les anciens clips
ralentis ne sont pas modifies ; ce pilote n'est pas un enregistreur continu.
Le superviseur accepte desormais une session limitee en duree (jusqu'a 24 h)
et une demande d'arret apres l'essai en cours. Un prototype de tache Windows
renouvelle ces sessions apres ouverture de session. Preparation DPAPI, reprise
et arret sont testes avec un superviseur factice, sans installation reelle.
Ces sorties ne constituent pas une endurance de 24 h.

## Baseline logicielle

- Backend : 33 tests API reussis en 2,62 s le 2026-09-12 sous Python 3.12.6.
  Ils isolent toujours les moteurs et le stockage, meme apres installation du runtime.
- Sept tests du proxy camera couvrent JPEG, extraction MJPEG fragmente,
  absence d'analyse/enrolement, reponses HTML/vides, erreurs 401/timeout et
  protection par cle API. Les reponses camera de ces tests sont simulees.
- Fixtures : configuration de test sans lecture du .env reel, stockage isole
  par test, detection Azure et traitements DeepFace simules, appels requests
  reseau interdits. Ce ne sont pas des tests d'integration Azure/ML.
- Contrat de posture : la reconnaissance reste autorisee hors regard frontal,
  conformement au comportement existant. Historique borne et isolation testes.
- Frontend : TypeScript, ESLint et build Next.js 16.2.6 reussis.
  Un avertissement ESLint subsiste pour l'image de camera HTTP (`no-img-element`).
- Regression navigateur avec API simulee : restauration de configuration IP,
  valeurs invalides, modification, rechargement et evenement storage verifies ;
  aucune erreur JavaScript non interceptee. Ces controles ne sont pas encore
  integres a une suite frontend persistante.
- CI ajustee pour installer [les dependances de test](../backend/requirements-test.txt)
  sur Python 3.11/3.12. La CI distante Linux n'a pas ete executee ici.
- Un avertissement de deprecation Starlette/AnyIO subsiste dans pytest.
  Les audits de dependances restent non bloquants et ne sont pas valides par ce lot.
- L'echec historique de `test_history_limit_is_bounded` est corrige : le tri
  utilise maintenant `created_at DESC, id DESC`. Un test force des dates identiques.
- Les erreurs Azure ne deviennent plus un resultat vide ; les comparaisons
  DeepFace incompletes echouent explicitement. L'auto-enrolement renvoie
  `recognized=false`, score zero, au lieu d'une fausse reconnaissance a 99 %.

L'environnement selectionne dans VS Code est `backend/.venv` (tests et runtime).
Le `.venv` Python 3.14 preexistant a la racine est conserve. Les dependances
globales Python 3.12 n'ont pas ete modifiees. Les versions directes de test sont
fixees, mais les dependances transitives ne constituent pas un verrou complet.
DeepFace 0.0.100, tf-keras et OpenCV 4.14.0.94 sont installes dans cet environnement.

### Preparation des essais de reconnaissance

- Backend loopback `127.0.0.1:8000`, frontend `127.0.0.1:3100`, CORS explicite.
  `/health` confirme Azure configure, DeepFace disponible, trois references
  conservees et action `log`. Aucune cle n'est inscrite dans les rapports.
- VGG-Face : chargement reel en 2,886 s ; comparaison de deux tableaux aleatoires
  identiques 224x224x3 avec detecteur ignore, distance zero, en 1,528 s.
  Cela prouve l'inference, pas la precision faciale ni la chaine avec detection.
- Azure : image grise synthetique 320x240, reponse valide sans visage en 1,15 s.
  Depuis le navigateur, POST reel equivalent en 630 ms, HTTP 200 ; CORS valide.
- Tableau de bord : analyse en pause au montage, zero envoi automatique observe.
  Bouton d'essai unique valide avec webcam synthetique ; resultat sans visage
  visible, puis retour en pause. Erreur 503 simulee : un seul appel, arret de la
  boucle et retrait du resultat precedent. Build final et lint reussis, un
  avertissement image IP preexistant. Affichage controle a 390 et 1440 pixels.
- Les instructions sont dans le [guide d'essai](../README.md#tester-la-reconnaissance-sur-ce-poste-windows).
  L'autorisation de webcam et les essais avec des personnes consentantes restent
  a faire dans le navigateur de l'utilisateur. Les tests n'ont pas modifie ses references.
- Les modeles demographiques preexistants et le temps complet avec plusieurs
  references ne sont pas valides. Premier chargement potentiellement long ; le
  frontend a un delai de 15 s. Ni liveness ni score d'identite calibre.
- Recherche, resumes IA et zones/alertes restent des travaux futurs du plan
  global. Le prototype d'acquisition planifiee est pret a installer, mais aucune
  tache reelle, endurance 24 h ou execution avant connexion n'est validee.

Commandes executees depuis la racine sous PowerShell :

```powershell
& ./backend/.venv/Scripts/python.exe -B -m pytest -c backend/pytest.ini --rootdir backend backend/tests -q -p no:cacheprovider
node frontend/node_modules/typescript/bin/tsc --project frontend/tsconfig.json --noEmit --incremental false
npm --prefix frontend run lint -- --no-cache
npm --prefix frontend run build
```

## Poste mesure

### Charge IA synthetique et concurrence

- Un test a reproduit le blocage de `/health` pendant les cinq secondes d'une
  detection simulee : la route asynchrone executait Azure et DeepFace directement.
  La route synchrone est maintenant deleguee au pool FastAPI, avec verrou
  non bloquant par processus. Une analyse concurrente recoit 429 et `Retry-After: 1`.
  Les tests couvrent reactivite de la sante et de l'historique, rejet concurrent,
  liberation apres succes/panne, et conservation du verrou apres annulation client.
  Utilisation locale avec un seul worker Uvicorn, pas de coordination multi-processus.
- Le premier essai avec detecteur OpenCV reel a revele une incompatibilite :
  OpenCV 5.0.0.93 ne contient aucun XML Haar dans son paquet, alors que
  DeepFace 0.0.100 exige les cascades visage et yeux. La dependance est bornee
  a `opencv-python<5`, environnement corrige en 4.14.0.94. Pas de contournement
  par desactivation du detecteur dans la reconnaissance.
- Script reproductible : `scripts/benchmark_recognition.py`, `--iterations 1..20`
  par taille de galerie (1 et 3). Base, captures et references synthetiques
  temporaires supprimees a la fin. Detection Azure simulee, demographie desactivee,
  vrai parcours crop/DeepFace/SQLite, actions externes et auto-enrolement desactives.
  Aucune image personnelle ni modification des trois references existantes.
- Mode prolonge : `--seconds-per-gallery 1..900`, exclusif avec `--iterations`,
  echeance controlee avant chaque requete, puis fin de la requete en cours.
  Les imports sont hors budget des galeries ; pas de watchdog d'inference native.
  Mesures par requete : latence, temps ecoule depuis le debut de galerie, RSS.
  Sept tests sans moteur ML verifient l'arret exact et le depassement par une
  derniere requete, le mode iterations, les bornes CLI et le refus d'ecrasement.
- Premier rapport : `backend/data/phase0-pilots/recognition-load-20260912.json`,
  6/6 requetes reussies. Rapport repete :
  `backend/data/phase0-pilots/recognition-load-40-20260912.json`, **40/40 HTTP 200**.
  Duree 72,516 s, dont imports 13,1611 s ; premiere requete 3,9832 s.
  1 reference a chaud : 19 mesures, mediane 0,665 s, plage 0,5809..0,7667 s.
  3 references : 20 mesures, mediane 2,0907 s, plage 1,7004..2,3407 s.
- Pic RSS observe 2675220480 octets (2,49 Gio), 459 echantillons sans indisponibilite.
  CPU observe 209,515 s, soit 288,92 % d'un coeur sur la duree totale imports compris.
  GPU TensorFlow : 0. Les services locaux etaient ouverts ; pas de capture camera
  ni de charge video simultanee pilotee pendant cette mesure.
- `/health` repond pendant les comparaisons : maximum observe 35,3 ms.
  Mesure ASGI en processus, sans reseau HTTP reel, transport Azure ni demographie.
  Elle n'inclut pas tous les delais d'ordonnancement avant une sonde et ne constitue
  pas un engagement de latence. Les images de bruit utilisent le repli sans visage
  d'OpenCV (`enforce_detection=False`), pas une detection reelle de personnes.
- Le backend local a ete redemarre apres correction du runtime. Pas de cache
  d'embeddings ni d'optimisation du cout lineaire en nombre de references.
  Les premiers modeles demographiques et le reseau peuvent toujours depasser
  le delai frontend de 15 s. Ni precision faciale, ni liveness, ni budget IA
  complet/24 h ne sont valides par ces essais synthetiques.

### Charge IA prolongee sur dix minutes

- Rapport : `backend/data/phase0-pilots/recognition-load-10min-20260912.json`.
  Commande : `--seconds-per-gallery 300` ; **951/951 HTTP 200**.
  710 requetes avec 1 reference en 300,0494 s, puis 241 avec 3 references
  en 301,0959 s. L'avant-derniere requete se termine avant 300 s dans les deux
  galeries : la derniere, deja engagee, explique le depassement autorise.
  Total observe 609,625 s ; imports 8,1781 s ; premiere requete 2,4961 s.
- Latences a chaud (premiere requete exclue uniquement pour la galerie 1) :
  medianes 0,4157 s et 1,211 s ; p95 par rang superieur 0,462 s et 1,4121 s.
  Medianes de la premiere minute puis des requetes terminees a partir de 240 s :
  0,4214 -> 0,41935 s pour 1 reference ; 1,281 -> 1,2051 s pour 3 references.
  Pas de ralentissement median observe entre ces fenetres. Aucune optimisation
  de reconnaissance entre essais ; l'ecart avec la serie courte n'est pas un gain
  causal attribuable au nouveau mode chronometre.
- RSS maximal echantillonne 2567000064 octets (2,39 Gio), 5387 echantillons,
  aucun indisponible. Medianes RSS des memes fenetres :
  2399,7305 -> 2401,0742 Mio (+1,34375 Mio), puis
  2401,4023 -> 2401,5078 Mio (+0,10547 Mio). Faible hausse sur dix minutes,
  pas une preuve d'absence de fuite. Le banc garde aussi ses mesures en memoire.
- CPU observe 3630,656 s, moyenne 595,56 % d'un coeur (environ six coeurs),
  GPU TensorFlow 0. Les latences incluent les sondes et le suivi des ressources.
  4435 sondes `/health` pendant l'analyse, maximum **0,5808 s** : conserver ce
  pic dans le bilan, sans engagement de latence. Une sonde HTTP du backend actif,
  distinct du processus de charge, a repondu en 7 ms depuis le navigateur.
- Meme isolation et limites que le banc court : aucune image personnelle,
  detection Azure simulee, demographie desactivee, base temporaire et aucune
  action externe. Pas de charge video simultanee controlee ni d'endurance 24 h.

### Concurrence video et IA synthetique

- Rapport : `backend/data/phase0-pilots/recognition-video-load-20260912.json`.
  Commande : `--seconds-per-gallery 60 --with-video` ; **203/203 HTTP 200**.
  149 requetes avec 1 reference en 60,3029 s puis 54 avec 3 en 60,7074 s.
  Duree totale 130,032 s ; imports 6,6544 s ; premiere requete 1,8774 s.
- Le banc reutilise `encode_hls` dans un thread pour alimenter FFmpeg avec une
  mire mobile 1280x720 a 15 images/s. Encodeur H.264 libx264 veryfast, sans audio.
  Deux clips de 900 images et 60 s, chacun 30 segments ; publication avant fin,
  ENDLIST et decodage integral verifies. Taille 7401782 puis 7401950 octets.
  Les medias et la base temporaire ont ete supprimes a la sortie du banc, sans
  toucher aux videos ni aux references de l'utilisateur.
- Chevauchement entre alimentation video et analyses : 60,0132 s puis 60,0087 s.
  Cette mesure est un chevauchement des intervalles de traitement, pas une mesure
  du temps CPU simultane. La derniere analyse peut finir apres la video.
  Medianes a chaud (sans la premiere requete de la galerie 1) : 0,3927 s et
  1,11785 s ; p95 par rang superieur : 0,4169 s et 1,1889 s.
  936 sondes de sante ASGI, maximum 0,0184 s.
- Pic RSS global echantillonne 2577940480 octets (2,40 Gio), 1140 echantillons
  sans indisponibilite. CPU observe 832,125 s, soit 639,94 % d'un coeur.
  Ces ressources incluent le processus du banc et les enfants observes ; les
  processus tres courts entre echantillons peuvent ne pas etre mesures.
  Le RSS par requete ne concerne que Python. Aucun GPU TensorFlow.
- Douze tests du banc passent en 6,77 s : modes CLI, echeances, refus
  d'ecrasement, chevauchement et nettoyage apres erreur d'analyse/encodage,
  plus HLS reel de quatre secondes. Le test FFmpeg est ignore si ses outils
  sont absents. Le producteur video est arrete en cas d'echec d'analyse ;
  l'encodage conserve le watchdog borne du pilote existant.
- Limites : mire simple, aucune camera, aucune image personnelle, Azure simule
  et demographie desactivee. Les images envoyees a DeepFace ne viennent pas de
  la video : il s'agit d'une charge concurrente, pas d'une integration de la
  chaine complete. Les essais distincts ne permettent pas d'attribuer un gain
  de latence ni de conclure a une absence de contention. Endurance 24 h ouverte.

### Materiel

| Element | Observation |
| --- | --- |
| CPU | AMD Ryzen 5 5600G, 6 coeurs / 12 threads |
| RAM | 31,8 Gio |
| GPU | RTX 3060, 12288 Mio VRAM, pilote 616.56 |
| Webcam | Logitech Webcam C930e, DirectShow |
| Node / npm locaux | 24.20.0 / 10.5.0 |
| Stockage libre apres essais | C: 26,0 Gio ; D: 11,8 Gio |
| FFmpeg / FFprobe | Distribution Gyan 9.0.1, installation compte utilisateur |

TensorFlow ne voit aucun GPU dans le runtime backend Windows teste.
VGG-Face fonctionne sur CPU ; les mesures synthetiques sont decrites plus haut.
NVENC fonctionne pour la video : cela ne prouve pas une acceleration DeepFace.

## Pilote USB court

[Script PowerShell](../scripts/test-usb-capture.ps1), independant de FastAPI,
du frontend, d'Azure et de DeepFace. Lors des premiers essais, la page du tableau
de bord a ete remplacee par une page vide ; le navigateur entier n'a pas ete ferme.

La C930e annonce MJPEG jusqu'a 1920x1080/30. Le mode YUYV brut annonce seulement
1280x720/10. Profil teste : entree MJPEG 1280x720/15, sortie H.264 yuv420p,
HLS/fMP4, segments d'environ deux secondes, audio desactive.

| Encodeur | Duree | Segments | Taille totale | Validation |
| --- | --- | --- | --- | --- |
| libx264 veryfast | 9,99999 s | 5 | 1,55 Mio | FFprobe et decodage complet reussis |
| h264_nvenc p4 | 10,066657 s | 5 | 0,65 Mio | FFprobe et decodage complet reussis |

Ces deux captures distinctes ne sont pas un benchmark comparatif de qualite,
de charge ou de debit durable. Le bitrate cible est 2 Mbit/s, pas un debit
constant garanti. L'avertissement swscale de format JPEG obsolete subsiste ;
la fidelite colorimetrique n'a pas ete evaluee.

```powershell
& ./scripts/test-usb-capture.ps1
& ./scripts/test-usb-capture.ps1 -Encoder h264_nvenc
```

Le script recherche FFmpeg dans PATH puis dans les liens WinGet du compte.
Chaque lancement cree un dossier unique sous `backend/data/phase0-pilots`
(ignore par Git) et affiche le chemin de la playlist. Il conserve les images
video locales, sans audio ni transmission cloud : les supprimer manuellement
apres inspection lorsqu'elles ne sont plus necessaires. Le script ne lance
aucun serveur ; le lecteur local optionnel peut servir ces videos. Fermer toute
application qui utilise directement la webcam avant l'essai, mais pas `/video`.
Le parametre `DurationSeconds` accepte 4 a 60 secondes de video ;
ce n'est pas un watchdog de duree murale en cas de blocage du peripherique.
Interrompre avec Ctrl+C si la capture se bloque.

Le premier essai a detecte un chemin Windows HLS incorrect ; le script utilise
desormais des separateurs `/`, et les deux essais suivants ont reussi.
Le controle initial de 512 Mio libres protege seulement ce pilote court :
il ne remplace ni une retention ni une surveillance continue de l'espace disque.

### Lecture USB pendant capture

Evolution du 2026-09-09 : HLS EVENT remplace VOD, avec publication par fichiers
temporaires puis renommage. Les profils utilisent `zerolatency` pour libx264
et `ll` pour NVENC. Le script verifie explicitement ENDLIST apres arret normal,
en plus de FFprobe et du decodage integral. Le lecteur est le meme que pour
le pilote D-Link : `npm --prefix frontend run dev:video`, puis
http://127.0.0.1:3100/video, sans backend de reconnaissance.

Nouveaux essais materiels C930e, 1280x720 a 15 images/s, sans audio :

| Encodeur | Duree obtenue | Segments | Taille totale | Validation |
| --- | --- | --- | --- | --- |
| libx264 veryfast/zerolatency | 20,066647 s | 10 | 4,98 Mio | ENDLIST, FFprobe, decodage integral |
| h264_nvenc p4/ll | 9,933323 s | 4 | 2,28 Mio | ENDLIST, FFprobe, decodage integral |

Pour la capture CPU `d1c45f18...`, le navigateur affichait le meme identifiant
selectionne, `Non finalise`, 1280x720, `readyState=4` et une position de 0,315 s
avec seulement deux segments dans la playlist et sans ENDLIST. Il a ensuite
atteint la fin a 20,067 s, `ended=true`, sans erreur. Cette preuve concerne la
webcam reelle, contrairement au test progressif D-Link sur rejeu.
Aucun appel de reconnaissance ou au proxy camera n'a ete observe sur `/video`.

Les deux encodeurs sont valides pour ce pilote court ; ce n'est pas un benchmark
comparatif. L'avertissement swscale de format JPEG reste present. Les profils
de resolution/cadence USB et D-Link sont distincts, mais partagent le lecteur.
Ce premier jalon ne testait ni supervision ni endurance. Les essais suivants
sont decrits ci-dessous ; le service permanent reste distinct.

## Supervision bornee et budget disque

Le [superviseur Python](../scripts/pilot_supervisor.py) reutilise les pilotes
USB PowerShell et MJPEG Python dans des sous-processus. Il accepte USB,
rejeu local cadence et URL D-Link authentifiee. Pas d'appel a Azure/DeepFace,
de modification des references, ni de serveur media lance par la supervision.

- 1 a 100 cycles ou une duree de session de 1 a 86400 s ; ces limites sont
  mutuellement exclusives. Clips de 4 a 60 s ; 0 a 3 reprises apres echec par cycle.
- Delai mural de chaque tentative : `30 + 3 * seconds`, encodage et validation
  compris. Une reprise attend 1, 2 puis 4 s au plus. Ce sont des cycles separes,
  pas une acquisition continue sans perte entre deux clips.
- Controle disque avant et pendant chaque tentative, environ toutes les secondes
  plus le temps des controles et mesures ; ce n'est pas une garantie temps reel.
  Plafond par defaut : 512 Mio sur tout le dossier, journaux et fichiers temporaires
  compris ; reserve : 2048 Mio ; marge supplementaire : 32 Mio.
- Aucune suppression automatique. Depassement, erreur de lecture ou lien dans
  le dossier : refus/arret, sans reprise automatique sur probleme de stockage.
  Ce n'est pas une reservation du disque ou un quota du systeme de fichiers.
- Verrou `.pilot-writer.lock` exclusif entre superviseurs. Les anciens scripts
  autonomes ne le consultent pas : ne pas les lancer dans ce dossier en parallele.
  Apres crash, verifier manuellement qu'aucun pilote/FFmpeg ne tourne avant de
  retirer un verrou abandonne. Un verrou existant n'est jamais efface automatiquement.
- Fin normale : le pilote verifie ENDLIST, codec/dimensions/duree et decodage.
  Delai, Ctrl+C ou stockage insuffisant : arret de l'arbre de processus actif,
  clip potentiellement incomplet conserve et non compte comme reussi.
- Mot de passe D-Link saisi une fois en mode masque, garde en memoire et transmis
  par stdin prive. Jamais dans les arguments, environnement ou fichiers ; le
  transport HTTP Basic sur le LAN reste non chiffre. HTTP 401/403 : arret sans reprise.
- Rapports JSON remplaces atomiquement et journaux locaux par tentative dans
  `backend/data/phase0-pilots/supervision/<identifiant>/`. L'API video ne sert
  ni les rapports, ni les journaux. Un rapport apres crash brutal peut rester
  `running` : il n'est pas une preuve qu'un processus est encore actif.

```powershell
& ./backend/.venv/Scripts/python.exe -m scripts.pilot_supervisor --usb --seconds 20 --cycles 3
& ./backend/.venv/Scripts/python.exe -m scripts.pilot_supervisor --usb --seconds 60 --cycles 5 --retries 1
& ./backend/.venv/Scripts/python.exe -m scripts.pilot_supervisor --url http://192.168.1.43/video/mjpg.cgi --seconds 20 --cycles 3
& ./backend/.venv/Scripts/python.exe -m pytest scripts/tests -q
```

### Mesures optionnelles et essais du 2026-09-12

`--metrics` ajoute `resources` a chaque tentative du rapport. Installer
`backend/requirements-test.txt` dans l'interpreteur utilise ; `psutil==7.2.2`
fournit les mesures de l'arbre du processus de capture, descendants inclus.
Sans cette option, aucun echantillonnage CPU/RAM n'est effectue.

- `peak_rss_bytes` : maximum echantillonne de la somme des memoires residentes.
  Des pages partagees peuvent etre comptees plusieurs fois ; ce n'est pas la RAM privee.
- `cpu_seconds_observed` : somme des derniers temps CPU observes par processus.
  Les processus courts ou termines entre deux mesures peuvent echapper au releve.
- `cpu_percent_one_core` : temps CPU observe divise par la duree de mesure,
  multiplie par 100. 100 % represente un coeur logique, pas la machine entiere.
  C'est une moyenne approximative, pas un pic CPU ni une mesure exhaustive.
- `samples`, `unavailable_samples` et `elapsed_seconds` permettent de lire
  la couverture. Aucune mesure GPU, charge globale, Azure ou DeepFace.

```powershell
& ./backend/.venv/Scripts/python.exe -m scripts.pilot_supervisor --usb --seconds 60 --cycles 10 --retries 1 --metrics
& ./backend/.venv/Scripts/python.exe -m scripts.pilot_supervisor --url http://192.168.1.43/video/mjpg.cgi --seconds 60 --cycles 3 --retries 1 --metrics
```

| Source reelle / libx264 | Cycles reussis sans reprise | Duree murale totale | Pic RSS par cycle | CPU moyen observe, un coeur |
| --- | --- | --- | --- | --- |
| C930e, 1280x720, 15 images/s | 10 x 60 s | 641,016 s | 190 a 192 Mio | 9,22 a 14,31 % |
| DCS-932LB, 320x240, lecture 15 images/s | 3 x 60 s de lecture | 95,297 s | 67 a 74 Mio | 14,44 a 16,50 % |

Les deux essais ont produit des clips sans audio, finalises et decodables.
Dernier clip USB : 59,99994 s, 30 segments, 14,53 Mio. Dernier clip D-Link :
900 images, 60 s de lecture, 30 segments, 15 221 006 octets, capture et validation
en 30,953 s, `published_before_eof=true`. Cette difference de duree D-Link
vient de la cadence de sortie imposee a 15 images/s ; ne pas assimiler ces
trois clips a trois minutes de surveillance reelle ni a une mesure de latence.
Une premiere supervision D-Link de 3 x 20 s de lecture a aussi reussi en 32,906 s.

Lecture du dernier clip D-Link `4b794e66...` verifiee apres finalisation dans
le navigateur : 320x240, duree 59,999999 s, fin atteinte sans erreur puis reprise
a 0,520 s, `readyState=4`. Ce controle ne prouve pas la lecture pendant capture.

Rapports locaux prives sous `backend/data/phase0-pilots/supervision/` :

- USB mesure : `afb963901a484a0f837efed98c024011/report.json`.
- D-Link mesure : `dc9df34b953d49f8bf4db0979cd560b2/report.json`.
- D-Link initial : `d01a5d6464244c2f8f1a81074c4249a9/report.json`.

Sur l'essai D-Link mesure : 89 controles disque, occupation maximale du dossier
329 038 993 octets, espace libre minimal 25 002 561 536 octets. Aucun fichier
supprime, aucun seuil depasse. Les captures restent separees par les validations
et reouvertures. La fermeture du navigateur entier n'a pas ete testee.

Suite des pilotes : 48 tests reussis en 5,93 s sous Python 3.12.6, dont trois
nouveaux tests du calcul CPU/RSS, de la disparition d'un processus et de la
persistance des mesures. Les 14 tests du superviseur passent egalement seuls.
La suite backend hors pilotes et la CI distante n'ont pas ete relancees.

### Correction de cadence du 2026-09-12

Pour les captures URL, `--seconds` mesure maintenant le temps ecoule depuis
la premiere image complete, avec une horloge monotone. La sortie reste a
15 ou 30 images/s : suppression des images excedentaires, repetition de la
derniere image disponible si la source est plus lente. Une fin de flux avant
la duree cible reste une erreur, et les timeouts reseau restent actifs.
Le rejeu local conserve sa duree calculee par nombre d'images et cadence.

Les rapports ajoutent `capture_seconds`, `received_frames` et
`timing_mode=monotonic-receive`. La connexion precede cette mesure ; la
finalisation et la validation suivent. L'horloge de reception n'est pas celle
du capteur : tampon reseau, groupement des images et blocage de l'encodeur
peuvent affecter les instants observes. Pas de mesure de latence capteur.
La reception URL est bornee a 128 Mio et `seconds * 120 + 1` images completes,
avec les memes limites par JPEG ; le rejeu garde sa limite de 32 Mio.

Validation : 53 tests pilotes reussis en 14,31 s sous Python 3.12.6. Les nouveaux
cas couvrent les sources a 10 et 30 images/s, les arrivees irregulieres, les
frontieres temporelles et la fin prematuree. L'integration HTTP authentifiee
avec FFmpeg recoit plus de 100 images en environ 4 secondes et produit 60
images a 15 images/s, soit 4 secondes de HLS, avec publication avant EOF et
decodage valide. Les reprises 503 et l'arret sans reprise sur 401 passent.
Confirmation materielle obtenue ensuite, detaillee ci-dessous.

### Confirmation materielle et pannes en cours de flux

Rapport prive : `supervision/0d28b8c511244d9eaadabc930a02c855/report.json`.
Trois cycles reussis a la premiere tentative, duree totale 186,375 secondes.

| Cycle | Images recues | Capture mesuree | Lecture | Images encodees |
| --- | --- | --- | --- | --- |
| 1 | 1820 | 60,032 s | 60 s | 900 |
| 2 | 1781 | 60,015 s | 60 s | 900 |
| 3 | 1818 | 60,047 s | 60 s | 900 |

Les trois clips : 320x240, 15 images/s, 30 segments, aucun audio, ENDLIST et
decodage verifies, `published_before_eof=true`. Le dernier clip
`c4c7b648bfda435dbc94f41a9632b3a1` est lu apres finalisation dans le navigateur,
avec avancement a 0,519829 s et aucune erreur. Cela ne prouve pas la lecture
navigateur pendant la capture reelle.

CPU moyen observe : 7,23 a 9,96 % d'un coeur ; pic RSS par cycle : 67,6 a
68,3 Mio. Les limites de l'echantillonnage restent celles decrites plus haut.
162 controles disque, occupation maximale 367 039 421 octets, espace libre
minimal 24 963 858 432 octets. Verrou libere apres l'essai, aucun clip supprime.

Tests supplementaires : serveur HTTP synthetique 320x240 a 30 images/s,
coupure apres environ 3 secondes ou blocage depassant le timeout de lecture.
Dans les deux cas, un HLS partiel sans ENDLIST demeure, la tentative echoue,
puis deux captures completes reussissent. Les rapports ne comptent pas le
clip partiel comme un succes et le verrou est libere. Ce ne sont pas des
tests de coupure electrique ou de reconnexion physique de la D-Link.
Suite complete : 55 tests reussis en 45,59 s sous Python 3.12.6.

### Endurance D-Link de trente minutes

Essai termine : rapport prive
`supervision/c44ff2d1086b41ad8dd4cf8580e317d1/report.json`.
30 cycles reussis a la premiere tentative, en 1 869,344 s, validations et
reouvertures incluses. Quota 1024 Mio, reserve 4096 Mio, aucune purge.

- Les 30 journaux confirment chacun 900 images, 320x240, 15 images/s, 60 s de
  lecture, 30 segments, aucun audio, ENDLIST, decodage et publication avant EOF.
- Capture mesuree par cycle : 60,000 a 60,062 s. CPU moyen observe par cycle :
  5,69 a 8,25 % d'un coeur ; pic RSS par cycle : 70 733 824 a 75 067 392 octets.
- 1 459 controles disque ; occupation maximale 696 691 989 octets, espace libre
  minimal 22 537 023 488 octets. Aucun verrou ni processus pilote/FFmpeg residuel.
- Lecture navigateur pendant la capture reelle `1aa8c78f94414987b9f4d991ef9a7899` :
  identifiant selectionne recoupe avec le catalogue non finalise et video a
  41,221 s, 320x240. La preuve concerne cette capture, pas un ancien clip finalise.
- Les installations et essais ponctuels ML ont partage le poste pendant une
  partie du palier. Ce n'est ni un benchmark isole ni une charge IA soutenue.

Ce palier ne demontre ni 24 heures d'endurance ni un enregistrement continu.
Il utilisait le mode limite a 100 cycles, avec pauses entre clips. Le mode
duree ajoute ensuite est decrit ci-dessous.

```powershell
& ./backend/.venv/Scripts/python.exe -m scripts.pilot_supervisor --url http://192.168.1.43/video/mjpg.cgi --seconds 60 --cycles 30 --retries 1 --metrics --quota-mib 1024 --reserve-mib 4096
```

Pour une repetition, verifier rapports, durees, CPU/RSS, seuils disque et
absence de processus ou verrou residuel. Conserver les captures interrompues
pour diagnostic. Le mot de passe est
saisi uniquement dans le terminal masque et n'est pas conserve entre essais.

### Session autonome bornee et arret gracieux

- `--run-seconds` remplace `--cycles` pour une session pouvant durer jusqu'a
  86400 s et depasser 100 cycles. L'horloge est monotone ; capture, encodage,
  validation et reprises comptent dans cette duree. L'essai deja lance termine
  sous son delai maximal, meme apres l'echeance ; aucune nouvelle tentative
  n'est lancee ensuite. Il ne s'agit pas d'une coupure stricte a la seconde.
- `--stop-file` designe un fichier initialement absent, dans un dossier existant.
  Sa creation demande l'arret apres l'essai en cours, y compris sa validation HLS.
  Le fichier n'est jamais supprime automatiquement. La demande peut aussi etre
  observee entre reprises ; l'attente de reprise reste bornee a quatre secondes.
- Le rapport enregistre `requested_seconds`, `requested_cycles`, `stop_file`
  et `stop_reason` : `duration_reached`, `stop_file` ou `cycles_completed`.
  `state=completed` decrit la fin normale de session, pas le succes de chaque
  tentative ; verifier aussi le compteur et les etats des tentatives.
- Le verrou, les quotas, la reserve, les delais par tentative et le refus de
  reprendre apres erreur d'authentification restent actifs. Aucun secret ajoute
  aux arguments, fichiers ou variables d'environnement ; aucune purge.
- Verification du 2026-09-12 : 29 tests de supervision et d'integration reussis
  en 59,00 s sous Python 3.12.6. Une horloge simulee couvre 105 cycles et
  l'echeance pendant un essai. Le serveur MJPEG local couvre six scenarios :
  503, 401, coupure, blocage, fichier d'arret et expiration de duree.
  Les deux nouvelles sorties finalisent chacune un clip de quatre secondes,
  60 images, ENDLIST et decodage verifies, sans seconde connexion camera.
  Les mots de passe sont synthetiques et le stockage isole par les tests.
- Protocole de lancement et d'arret dans le
  [guide de supervision](../README.md#supervision-born%C3%A9e-et-stockage).
  Aucune camera reelle ni requete Azure n'a ete lancee dans ce lot.

Ce processus direct fonctionne sans navigateur mais exige un terminal ouvert.
Les scripts `install-camera-task.ps1` et `run-camera-task.ps1` ajoutent un mode
planifie pour l'utilisateur courant : secret DPAPI, ACL limitee a l'utilisateur
et SYSTEM, sessions renouvelees, journal rotatif et reprise apres echec. Les
scripts `status-camera-task.ps1`, `stop-camera-task.ps1` et
`uninstall-camera-task.ps1` separent l'observation, l'arret gracieux et la
suppression de la tache ; la desinstallation conserve l'etat par defaut.

Les tests Windows non destructifs preparent la configuration sans enregistrer
de tache, provoquent un code 7, verifient la seconde session, l'arret par fichier,
le refus d'un secret DPAPI corrompu et d'un chemin d'arret hors du dossier d'etat.
La tache utilise `AtLogOn` avec un jeton interactif : elle redemarre apres une
nouvelle connexion, pas avant connexion, et n'est pas un service systeme.
Windows doit rester eveille. Le 12 septembre 2026, la tache reelle a ete
installee et demarree. L'ACL du dossier reel `%LOCALAPPDATA%` a impose
`FileSystemAclExtensions.SetAccessControl` afin de ne pas demander le privilege
SACL `SeSecurityPrivilege`; le canal prive accepte desormais les fins de ligne
LF et CRLF de `.NET WriteLine()`. La suite complete compte 83 tests reussis.
Le reboot et l'endurance 24 h restent a valider avec un budget disque explicite.

### Resultats locaux du 2026-09-09

- Suite complete des pilotes : 45 tests reussis en 6,16 s sous Python 3.12.6,
  dont les deux integrations HTTP/FFmpeg reelles sur source synthetique.
  Aucune erreur signalee dans les fichiers modifies. La CI Linux distante
  et la suite backend hors pilotes n'ont pas ete relancees dans ce lot.
- 3 cycles USB de 20 s : tous finalises et decodes, sans reprise.
- 5 cycles USB de 60 s : tous finalises et decodes, sans reprise, lecteur
  remplace par `about:blank` pendant tout l'essai. Duree murale 316,609 s,
  comprenant validations et reouvertures. Il existe donc des pauses de capture.
- 312 controles disque sur ce dernier essai ; occupation maximale observee
  du dossier complet 115 808 242 octets (110,44 Mio), anciennes captures incluses ;
  espace libre minimal 11 606 487 040 octets (10,81 Gio).
- Dernier clip : 60,066607 s, 1280x720, 15 images/s, 30 segments, 14,53 Mio,
  sans audio, ENDLIST et decodage verifies. Avertissement JPEG swscale toujours present.
- Rapport prive : `supervision/77a9d1c5daa84fd9b83b3fac4e557b73/report.json`.
- Tests stockage : seuils exacts, fichiers temporaires, reserve insuffisante,
  erreur de scan, liens, verrou concurrent et liberation apres interruption.
- Tests supervision : reprises puis succes, limite de tentatives, arret sur
  stockage insuffisant avant et pendant capture, interruption, delai avec
  arret d'un vrai processus Windows, transmission privee sans secret dans les sorties.
- Integration HTTP locale avec FFmpeg reel : reponse 503 puis deux captures
  MJPEG/HLS reussies ; reponse 401 refusee sans nouvelle tentative. Les images
  et identifiants de ce test sont synthetiques, pas ceux de la D-Link.
  Ces tests sont ignores si FFmpeg/FFprobe ne sont pas installes.

La tache reelle a finalise trois clips autonomes consecutifs de 60 secondes,
320x240, H.264 et 30 segments, navigateur non requis. Une frame a traverse
Azure Face avec `AUTO_ENROLL=false` : aucun visage n'etait present et les trois
references sont restees inchangees, donc ce test ne valide pas DeepFace sur un
visage camera. Ce lot ne prouve pas la reconnexion apres debranchement physique,
l'endurance 24 h ni le redemarrage Windows. Le disque D: disposait de 20,87 Gio
libres, soit 16,87 Gio apres la reserve de 4 Gio, contre environ 21,6 Go par jour
au debit de dimensionnement. La tache a donc ete arretee proprement apres la
recette ; elle reste installee. Une politique de stockage est necessaire avant
une endurance 24 h.

## Camera IP D-Link identifiee

L'utilisateur a fourni une DCS-932LB a l'adresse locale `192.168.1.43`.
La [fiche officielle D-Link](https://www.dlink.com/fr/fr/products/dcs-932l-day-night-cloud-camera)
indique du MJPEG avec une resolution maximale de 640x480, une fin de vie au
28/07/2020 et une fin de support au 28/07/2022.

Controles du 2026-09-08, sans utiliser les identifiants de la capture partagee :

- Ports TCP 80 et 443 ouverts ; connexion au port standard RTSP 554 impossible.
  Ce resultat ne prouve pas l'absence de RTSP sur tout autre port.
- Interface web accessible sur HTTP. TLS et certificat non verifies.
- `/image/jpeg.cgi` et `/video/mjpg.cgi` repondent HTTP 401 avec authentification
  Basic. Ces reponses seules ne prouvent pas le contenu des flux.
- Un essai JPEG authentifie ulterieur, avec saisie masquee du mot de passe
  directement dans le terminal, a renvoye HTTP 200, image/jpeg, 7 348 octets.
  FFprobe confirme 320x240 ; le decodage FFmpeg avec `-xerror` reussit.
  Aucune image n'a ete envoyee a Azure/DeepFace ni enrolee lors de cet essai.
- La cadence durable et l'integration reelle au dashboard restent a verifier.
  Aucun materiel RTSP supplementaire n'est requis.

Reprise du 2026-09-09 : apres une expiration de connexion, l'utilisateur a
rallume la camera. `/video/mjpg.cgi` repond de nouveau ; un essai authentifie
a recu HTTP 200, `multipart/x-mixed-replace`, 1 477 906 octets en 10,013389 s.
Le mot de passe a ete saisi uniquement dans le terminal. La limite curl de
10 secondes a interrompu volontairement le flux (code 28), sous le plafond
de 10 Mio. Aucune image n'a ete envoyee a l'IA.

FFprobe compte 310 images MJPEG de 320x240. Le decodage integral avec FFmpeg
`-xerror` echoue au demultiplexage ; le decodage strict des 250 premieres
images reussit avec `-readrate 1 -frames:v 250`. Ces resultats sont compatibles
avec une fin multipart incomplete apres interruption, mais ne valident pas
l'integrite du fichier entier. Le rapport images/temps de transfert n'est pas
une mesure fiable de cadence durable ou d'horodatage camera.

La capture privee est conservee sous `backend/data/phase0-pilots` (ignore par
Git), sans exposition par un serveur. Ce test prouve la reception et le
decodage court, pas un enregistrement HLS ni l'endurance navigateur ferme.

Changer le mot de passe expose et saisir le nouveau uniquement localement.
Ne pas ajouter les identifiants aux fichiers suivis, aux URL ou aux journaux.
Basic sur HTTP ne chiffre pas les identifiants : limiter les essais a un reseau
local de confiance/isole et ne pas exposer cette camera hors support a Internet.
Le format MJPEG devra etre encode en H.264 pour le pilote HLS/fMP4, contrairement
a une camera RTSP fournissant deja un flux H.264 copiable.

## Premier pilote D-Link HLS avec finalisation

Premiere version du [script Python](../scripts/ip_camera_pilot.py), independant
de FastAPI, du frontend et de l'IA. `python-multipart` assemble les parties
completes ; Pillow valide chaque JPEG avant encodage. Les JPEG etaient collectes
en memoire, puis transmis par stdin a FFmpeg, sans URL ni identifiant dans ses arguments.
La fermeture de stdin permet a FFmpeg de terminer proprement le HLS/fMP4.

Essais du 2026-09-09 avec libx264 veryfast, H.264 yuv420p, sans audio :

| Source | Collecte | Images | Lecture a 30 images/s | Segments | Taille totale |
| --- | --- | --- | --- | --- | --- |
| Rejeu local du MJPEG precedent | 0,062 s | 120 | 4,0 s | 2 | 572 451 octets |
| DCS-932LB en direct | 9,578 s | 300 | 10,0 s | 5 | 2 352 832 octets |

Pour les deux essais : resolution 320x240, balise `EXT-X-ENDLIST` presente,
codec/dimensions/nombre d'images/duree verifies par FFprobe et decodage HLS
integral avec FFmpeg `-xerror` reussi. La fin tronquee de l'ancien fichier
multipart n'est pas transmise a l'encodeur. NVENC n'a pas ete teste sur ce pilote.

```powershell
& ./backend/.venv/Scripts/python.exe -B scripts/ip_camera_pilot.py --url http://192.168.1.43/video/mjpg.cgi --seconds 10
& ./backend/.venv/Scripts/python.exe -B -m pytest scripts/tests/test_ip_camera_pilot.py -q -p no:cacheprovider
```

Le mot de passe n'est accepte que par invite masquee, sans sauvegarde. Les
redirections HTTP et les proxies d'environnement sont desactives. Les captures
et HLS prives restent dans un dossier unique sous `backend/data/phase0-pilots`.
La version initiale exige 512 Mio libres et limite la reception a 32 Mio, chaque JPEG a
2 Mio et 1920x1080 ; les dimensions doivent rester constantes et paires.
Les delais reseau sont de 5 s en connexion et 3 s en lecture. La collecte
controle une echeance de `2 * seconds + 5` entre lectures ; ce n'est pas un
watchdog systeme. Les sous-processus d'encodage/verification ont un timeout.

Dans cette version initiale, la cadence (15 ou 30 images/s, 30 par defaut) est imposee, sans
conserver les horodatages source. Ce traitement en deux etapes prouve la
compatibilite JPEG -> H.264/HLS, pas une acquisition/lecture simultanee, un
service autonome ou une mesure de latence en direct. Aucun appel Azure/DeepFace,
aucun enrolement, aucun test navigateur entier ferme ou d'endurance effectue.

## Encodage progressif et lecteur local

Evolution du 2026-09-09 : le parseur fournit maintenant un iterateur de JPEG
valides. Chaque image est transmise immediatement a FFmpeg, sans conserver toute
la capture en RAM. HLS EVENT avec segments de 2 s et publication temporaire/renommage
permet la lecture pendant l'encodage. Un watchdog arrete FFmpeg en cas de blocage
du sous-processus ; les limites reseau et volume du pilote restent appliquees.

La route `/video` du frontend liste les captures et les lit avec `hls.js`, avec
repli natif si MediaSource est absent. L'API `/api/pilot-video` est desactivee
sans `HLS_PILOT_ROOT`. Le lanceur `npm --prefix frontend run dev:video` configure
le dossier des pilotes et ecoute uniquement sur `127.0.0.1:3100`.
Le backend de reconnaissance peut rester arrete. Aucun montage public du dossier
de donnees : seuls les identifiants et noms de fichiers HLS autorises sont servis,
avec confinement par chemin reel, limites de taille et controles d'origine.
Ce n'est pas une authentification : ne pas publier ce lanceur sur le LAN ou
derriere un proxy. La liaison loopback est une condition de securite du pilote.

Validation sur le MJPEG deja capture, rejoue progressivement, pas une nouvelle
connexion authentifiee a la camera :

| Rejeu | Images | Segments | Taille totale | Temps total avec verification |
| --- | --- | --- | --- | --- |
| 10 s a 30 images/s | 300 | 5 | 1 593 841 octets | 10,297 s |
| 20 s a 15 images/s | 300 | 10 | 1 618 461 octets | 20,250 s |

Les deux sorties 320x240 sans audio passent FFprobe, le decodage integral
FFmpeg et la verification ENDLIST ; `published_before_eof` est vrai.
Sur le second essai, le navigateur lit la capture `addc4ef2...` a 0,313 s,
`readyState=4`, alors que sa playlist ne contient que deux segments et aucune
balise ENDLIST. Il atteint ensuite 20 s, duree finie, `ended=true`, sans erreur.
Ce test demontre la lecture concurrente sur rejeu, pas la latence de la camera.

Le HLS natif du navigateur de test conservait une duree infinie apres ENDLIST ;
la priorite a `hls.js` corrige ce cas observe. Le repli natif reste a valider
sur les navigateurs sans MediaSource. Un premier controle de 10 s n'avait pas
observe la fenetre non finalisee ; le controle instrumente de 20 s a abouti.

Verifications : 12 tests Python du pilote (parseur progressif, limites et
nettoyage du processus), 3 tests TypeScript de desserte (activation, origine,
chemins, liens sortants, tailles), build Next.js et lint du lecteur reussis.
Les tests sont ajoutes a la CI ; son execution distante n'a pas ete lancee ici.
Controles navigateur 1440x900 et 390x844 : image visible, pas de debordement,
lecture/pause/recherche temporelle, erreur simulee puis reprise. Aucun appel
Azure/DeepFace, `/analyze-face` ou `/proxy-camera` observe sur cette page.
L'installation npm signale 8 vulnerabilites (1 moderee, 6 elevees, 1 critique),
non corrigees dans ce lot. L'echec anterieur de `test_history_limit_is_bounded`
reste hors lot ; la suite backend complete n'a pas ete revalidee.

## Conditions restantes avant validation complete

1. Valider la lecture navigateur pendant une capture D-Link reelle ; la
  publication avant fin d'acquisition est prouvee par le pilote, mais la preuve
  navigateur simultanee reste limitee au rejeu et a l'USB. La correction de
  cadence est maintenant confirmee sur la camera. Saisir les identifiants uniquement
  localement, jamais dans le chat.
2. Choisir un volume et un quota video avec reserve libre. D: est trop contraint
   pour autoriser un enregistrement continu sans politique de stockage.
   A 2 Mbit/s constants, une seule source represente environ 21,6 Go par jour
   hors surcout ; ce calcul est indicatif, pas une mesure de ces captures.
3. Tester la DCS-932LB navigateur entier ferme, les coupures/reconnexions et
  les permissions du compte Windows. Le pilote USB reste optionnel.
4. Completer les mesures CPU/RAM des pilotes par la charge globale, le GPU,
  les latences DeepFace selon le nombre de references, les quotas et le budget
  Azure. Aucun appel Azure reel effectue ici.
5. Valider le redemarrage de la tache `AtLogOn` et l'endurance 24 h de cette
  camera apres choix d'un stockage suffisant. La tache installee enchaine les
  sessions avec reprises apres connexion Windows ; le demarrage avant connexion,
  le service systeme et la politique de retention ne sont pas implementes.

Le parcours de reconnaissance conserve son acquisition pilotee par le navigateur. Les risques
deja identifies (identifiants IP en localStorage, cle publique, proxy arbitraire,
auto-enrolement assimile a une reconnaissance) restent a traiter dans les phases
suivantes avant exploitation permanente ou exposition reseau.