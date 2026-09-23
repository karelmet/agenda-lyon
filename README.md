# Agenda spectacles & concerts – Lyon

Calendrier `.ics` mis à jour chaque lundi, auquel on s'abonne depuis l'iPhone.

## Installation (≈ 15 min, une seule fois)

1. **Clé Ticketmaster** (gratuite) : crée un compte sur https://developer.ticketmaster.com,
   puis récupère la « Consumer Key » dans *My Apps*.
2. **Dépôt GitHub** : crée un dépôt **public** (ex. `agenda-lyon`) et envoie-y ces fichiers.
3. **Secrets** : *Settings → Secrets and variables → Actions → New repository secret*
   - `TICKETMASTER_API_KEY` (obligatoire)
   - `ANTHROPIC_API_KEY` (optionnel : descriptions rédigées par Claude, quelques centimes par semaine)
   - `OPENAGENDA_KEY` + variable `OPENAGENDA_AGENDAS` (optionnel, voir plus bas)
4. **Pages** : *Settings → Pages → Deploy from a branch → main / docs*.
5. **Premier lancement** : onglet *Actions → Mise à jour du calendrier → Run workflow*.
6. **iPhone** : Réglages → Apps → Calendrier → Comptes de calendrier → Ajouter un compte
   → Autre → Ajouter un calendrier avec abonnement, puis colle :
   `https://TON-PSEUDO.github.io/agenda-lyon/lyon-evenements.ics`

## Ajouter les petites salles (OpenAgenda, optionnel)

Beaucoup de théâtres et salles lyonnaises publient sur https://openagenda.com.
Crée un compte, génère une clé API, cherche les agendas qui t'intéressent et
note leur identifiant (UID) visible dans les réglages de l'agenda. Mets-les dans la
variable `OPENAGENDA_AGENDAS`, séparés par des virgules.

## Réglages

Variables d'environnement lues par le script : `RADIUS_KM` (40 par défaut),
`HORIZON_DAYS` (180 par défaut).

Test en local : `TICKETMASTER_API_KEY=xxx python lyon_events.py`
