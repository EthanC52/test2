# Audit des sources de données

## Ethereum — Etherscan API V2 Free tier

Endpoints utilisés :

- dernier bloc : `module=proxy&action=eth_blockNumber` ;
- `decimals()` : `module=proxy&action=eth_call&data=0x313ce567` ;
- `totalSupply()` : `module=proxy&action=eth_call&data=0x18160ddd` ;
- transferts : `module=account&action=tokentx&contractaddress=...`.

Références officielles :

- <https://docs.etherscan.io/api-reference/endpoint/ethblocknumber>
- <https://docs.etherscan.io/api-reference/endpoint/ethcall>
- <https://docs.etherscan.io/api-reference/endpoint/tokentx>
- <https://docs.etherscan.io/resources/rate-limits>
- <https://docs.etherscan.io/changelog>
- <https://docs.etherscan.io/resources/pro-endpoints>

`tokenholdercount` est PRO et n’est pas appelé. Le Free tier permet `tokentx`, avec une
limite actuelle de 1 000 résultats par page et 3 appels/seconde. Le code fixe un bloc cible confirmé, pagine jusqu’à épuisement, rejoue les mints, burns et transferts, exclut l’adresse zéro du compteur puis compare la somme des balances à `totalSupply()`. Le premier run crée un checkpoint pseudonymisé; les suivants ne lisent que la plage de blocs nouvelle. Une incohérence déclenche un replay complet.

## Solana — Solscan Playground Free tier

- Endpoint unique : `GET https://pro-api.solscan.io/playground/token/meta`.
- Paramètre : `address=<mint>`.
- Authentification : header `token: <SOLSCAN_API_KEY>`.
- Champs utilisés : `decimals`, `supply`, `holder`.
- Référence : <https://pro-api.solscan.io/pro-api-docs/v2.0/playground/v2-token-meta>

Aucune transaction historique, signature, liste de comptes SPL ni API Helius n’est lue.
Le dashboard ne conserve que la valeur retournée au moment du run.

## XRP Ledger — XRPLCluster + `account_lines`

- Endpoint par défaut : `https://xrplcluster.com/`.
- Méthode : `account_lines` sur le compte issuer.
- Première page : `ledger_index=validated`.
- Pages suivantes : réutilisation du `ledger_hash` retourné.
- Références :
  - <https://xrpl.org/docs/references/http-websocket-apis/public-api-methods/account-methods/account_lines>
  - <https://xrpl.org/docs/concepts/tokens/fungible-tokens/trust-line-tokens>

Depuis la perspective de l’issuer, un solde négatif signifie que le peer détient la
quantité positive correspondante. La supply est la somme des valeurs absolues de ces
soldes. Les trust lines à zéro et les soldes positifs issuer-side ne sont pas des holders.
Le code protège aussi contre les markers répétés et les changements de ledger.

## Stellar — Horizon officiel

- Asset : `/assets?asset_code=EURCV&asset_issuer=<issuer>`.
- Comptes : `/accounts?asset=EURCV:<issuer>` avec pagination.
- Références :
  - <https://developers.stellar.org/docs/data/apis/horizon/api-reference/resources/assets/object>
  - <https://developers.stellar.org/docs/data/apis/horizon/api-reference/list-all-accounts>

La supply additionne les balances par état d’autorisation ainsi que
`claimable_balances_amount`, `contracts_amount` et `liquidity_pools_amount`.

Les holders additionnent :

- les comptes classiques dont la balance est strictement positive ;
- `num_contracts` ;
- `num_liquidity_pools`.

Les claimable balances restent dans la supply mais pas dans les holders, car un objet
claimable peut avoir plusieurs claimants et ne représente pas une adresse détentrice
unique. Le range de ledgers Horizon observé pendant le run est conservé dans les détails.

## Historique

Aucune API n’est interrogée pour reconstruire les anciennes supplies du graphique.
L’historique provient des JSON du dépôt, migrés sans perte puis prolongés avec le total courant à chaque run exploitable. Les anciens points journaliers sont conservés et les nouveaux points sont horodatés en UTC. Une chaîne indisponible est forward-fillée avec sa dernière valeur connue ; si aucune valeur n’existe encore pour cette chaîne, le point du run est omis afin de ne pas créer une baisse artificielle.
