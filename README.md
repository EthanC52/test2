# CoinVertible Stablecoin Dashboard — GitHub Pages autonome

Le dashboard affiche uniquement, pour EURCV et USDCV :

1. les holders actuels agrégés entre chaînes, avec la chaîne qui en compte le plus ;
2. la supply courante dans la devise d’ancrage du token ;
3. la capitalisation nominale historique, construite exclusivement avec les snapshots de supply du dépôt.

Le dépôt est conçu pour être publié sur GitHub Pages et rafraîchi automatiquement deux
fois par heure par GitHub Actions.

## Fonctionnement toutes les 30 minutes

Le workflow `.github/workflows/update_data.yml` s’exécute à `:07` et `:37` UTC :

1. il récupère chaque source indépendamment ;
2. il conserve la dernière valeur connue lorsqu’une source échoue ;
3. il écrit atomiquement `data/current.json` ;
4. il ajoute un point horodaté aux historiques dont le total est exploitable ;
5. il commit les JSON et le checkpoint Ethereum ;
6. il construit `_site/` avec seulement `index.html`, `assets/` et les JSON publics ;
7. il déploie cet artefact sur GitHub Pages.

Le cron GitHub n’est pas une horloge temps réel : un run peut être retardé. Les points
historiques utilisent donc l’heure UTC réelle du fetch, et non une heure théorique.

## Sources

| Réseau | Source autorisée | Supply courante | Holders courants |
|---|---|---|---|
| Ethereum | Etherscan API V2, Free tier | `totalSupply()` via `proxy/eth_call` | balances reconstruites depuis `account/tokentx` |
| Solana | Solscan Playground, Free tier | `supply` normalisé avec `decimals` | champ `holder` du même appel `token/meta` |
| XRP Ledger | JSON-RPC XRPLCluster | somme des soldes négatifs vus depuis l’issuer | peers avec solde issuer-side strictement négatif |
| Stellar | Horizon mainnet officiel | balances + claimable balances + contrats + liquidity pools | comptes positifs + contrats + liquidity pools |

L’audit détaillé est dans [`SOURCE_AUDIT.md`](SOURCE_AUDIT.md).

## Ethereum avec une clé Etherscan gratuite

Les endpoints `tokenholdercount` et `tokenholderlist` sont PRO et ne sont jamais appelés.
Le code utilise uniquement les endpoints accessibles au Free tier :

- `eth_blockNumber` ;
- `eth_call` pour `decimals()` et `totalSupply()` ;
- `tokentx` paginé par lots de 1 000.

Pour rendre un run toutes les 30 minutes réaliste :

- le premier run rejoue tous les transferts jusqu’au bloc cible ;
- les runs suivants ne demandent que les transferts postérieurs au checkpoint ;
- le checkpoint stocke des clés d’adresses hachées et les balances brutes ;
- le checkpoint reste dans `.state/etherscan/` et n’est jamais copié dans l’artefact Pages ;
- si le checkpoint est absent, corrompu ou incohérent avec `totalSupply()`, un replay
  complet est automatiquement tenté ;
- par défaut, le bloc cible est le dernier bloc moins 12 confirmations afin d’éviter de
  figer un état susceptible d’être réorganisé.

Sur un run calme, le gros checkpoint n’est pas réécrit : le prochain run rescannera
simplement une plage vide ou courte. Cela réduit fortement le bruit Git.

## Historique conservé et prolongé

La migration conserve les anciens points journaliers au format :

```json
{"date": "2025-01-01", "supply": "123"}
```

Les nouveaux runs ajoutent des points intrajournaliers :

```json
{"timestamp": "2026-07-27T10:37:00Z", "supply": "125"}
```

Les deux formats coexistent dans les mêmes fichiers. Aucun ancien point n’est supprimé.
Un point n’est ajouté que si toutes les chaînes du token ont une valeur fraîche ou une
dernière valeur connue, et qu’au moins une chaîne a réellement été rafraîchie. Une panne
d’API ne peut donc pas créer une chute artificielle de supply.

## Résilience

Chaque chaîne est isolée :

- une source saine est actualisée même si une autre échoue ;
- une source en erreur réutilise sa dernière valeur de `current.json`, avec le statut
  `stale` et le détail de l’erreur ;
- sans valeur antérieure, la chaîne est marquée manquante sans bloquer les autres ;
- `current.json` est publié lors d’un run partiel ;
- un historique JSON invalide est ignoré lors du build Pages, sans masquer les données
  courantes ni l’autre courbe ;
- les écritures JSON utilisent un remplacement atomique ;
- un échec de push Git après trois tentatives n’empêche pas le déploiement du fetch déjà
  produit, même si le run suivant pourra devoir rescanner davantage de blocs Ethereum.

## Installation sur le dépôt existant

Ne supprimez pas le dossier `data/` existant avant la migration. Copiez ce refactor à la
racine du dépôt, puis exécutez une fois :

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/migrate_legacy_data.py --clean
python -m pytest -q
```

La migration fusionne les historiques compacts déjà présents avec les anciens JSON par
chaîne, en appliquant le forward-fill avant agrégation. Elle supprime ensuite les anciens
holders, taux de change, fetchers dupliqués, `.claude` et autres fichiers devenus inutiles.

## Configuration GitHub

Dans **Settings → Secrets and variables → Actions**, ajoutez deux secrets de dépôt :

- `ETHERSCAN_API_KEY` ;
- `SOLSCAN_API_KEY`.

Dans **Settings → Pages → Build and deployment**, sélectionnez **GitHub Actions** comme
source. Lancez ensuite une première fois le workflow **Refresh data and deploy Pages**
depuis l’onglet Actions. Les runs programmés utilisent toujours la branche par défaut.

Le workflow possède les permissions nécessaires pour committer les snapshots et déployer
Pages. Si la branche `main` interdit les pushes du `GITHUB_TOKEN`, autorisez GitHub Actions
à écrire dans le dépôt ou adaptez la règle de protection.

## Données publiques et internes

Données servies par Pages :

```text
data/current.json
data/history/eurcv.json
data/history/usdcv.json
```

État interne non publié :

```text
.state/etherscan/<contract>.json
```

Les nombres décimaux sont sérialisés en chaînes afin de préserver leur précision.

## Variables d’environnement

Obligatoires :

- `ETHERSCAN_API_KEY` ;
- `SOLSCAN_API_KEY`.

Optionnelles :

- `XRPL_RPC_URL` ;
- `STELLAR_HORIZON_URL` ;
- `ETHERSCAN_MIN_INTERVAL_SECONDS` — `0.36` par défaut ;
- `ETHERSCAN_MAX_TRANSFER_PAGES` — `10000` par défaut ;
- `ETHERSCAN_CONFIRMATIONS` — `12` par défaut ;
- `ETHERSCAN_STATE_DIR` — défini automatiquement par `scripts/fetch.py`.

## Validation locale

```bash
python -m pytest -q
python -m compileall -q scripts tests
node --check assets/app.js
python scripts/build_site.py --output _site
python -m http.server 8000 --directory _site
```

Le total de holders est une somme inter-chaînes, pas une déduplication d’identités.
