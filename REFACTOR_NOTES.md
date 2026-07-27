# Décisions du refactor GitHub Pages

## Supprimé

- Le dossier `.claude`.
- Helius et tout replay historique Solana.
- L’historique des holders sur toutes les chaînes.
- Les anciens états `*_state.json` et listes `*_known_holders.json`.
- `eurusd_rates.json`, le sélecteur de devise et toute conversion.
- Les séries de market cap dépendant d’un taux externe.
- Les fetchers dupliqués et les JSON intermédiaires.

## Conservé sans perte

- Tous les snapshots de supply déjà présents dans les anciens JSON.
- Les snapshots compacts déjà migrés, prioritaires en cas de clé temporelle identique.
- Le forward-fill par chaîne lors de l’agrégation historique initiale.
- Les anciens points `date` et les nouveaux points `timestamp` dans un même historique.

## Cadence

- GitHub Actions lance le fetch à `:07` et `:37` UTC.
- Chaque run valide ajoute un point horodaté, au lieu de remplacer le seul point du jour.
- `current.json` représente le dernier run, complet, partiel, stale ou indisponible.
- Le build Pages n’embarque que le frontend et les trois JSON publics.

## Ethereum Free tier

Le compteur PRO a été remplacé par un replay de `account/tokentx`. Le premier run crée un
checkpoint pseudonymisé dans `.state/etherscan/`; les runs suivants ne lisent que les
nouveaux blocs. `decimals()` et `totalSupply()` sont lus au bloc cible avec `eth_call`.

Le checkpoint est une optimisation vérifiée, pas une source de vérité : si sa somme ne
correspond plus à `totalSupply()`, le code tente automatiquement un replay complet. Les
adresses sont stockées sous forme de SHA-256 et l’état interne n’est jamais déployé sur
Pages.

## Résilience

- Les sources sont exécutées indépendamment.
- Une source en erreur réutilise sa dernière valeur connue avec le statut `stale`.
- Les sources saines continuent d’être mises à jour.
- Un total réellement incomplet n’est jamais ajouté à l’historique.
- Une erreur d’un historique ne bloque pas l’autre.
- Les écritures restent atomiques.
- Un historique malformé est exclu de l’artefact Pages sans bloquer les cartes courantes.
- Le push Git est retenté trois fois et son échec final n’annule pas le déploiement produit.

## Sources corrigées

- XRPL : seuls les soldes négatifs vus depuis l’issuer représentent une détention
  positive du peer ; les trust lines à zéro sont exclues.
- Stellar : la supply inclut claimable balances, contrats et liquidity pools.
- Précision : tous les calculs Python utilisent `Decimal`, jamais `float`.

## Limite volontaire

Le total de holders additionne les chaînes et ne déduplique pas les identités.
