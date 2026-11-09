# Pubblicazione automatica su Facebook e Instagram

Pubblica testo + locandina sulla Pagina Facebook e sull'account Instagram collegato,
alle date e orari scritti in `post.yaml`. Gira gratis su GitHub Actions ogni 15 minuti:
non serve nessun computer acceso.

## Come si usa (tutti i giorni)

1. Carica la locandina nella cartella `locandine/`.
2. Aggiungi una voce in `post.yaml` (testo, immagine, elenco di date).
3. Salva (commit). Fine: alle date indicate il post esce su Facebook e Instagram.

Per vedere cosa è uscito: file `stato/pubblicati.json`, oppure la scheda **Actions** del repository.
Se una pubblicazione fallisce, GitHub ti manda una mail; il sistema riprova da solo fino a 3 volte.

Per Instagram la locandina viene adattata in automatico (JPEG, proporzioni accettate):
una locandina A4 riceve due bande laterali dello stesso colore del bordo.
Facebook riceve la locandina originale.

## Configurazione iniziale (una volta sola)

### 1. Repository GitHub

- Crea un repository **pubblico** (serve perché Instagram scarica la locandina da un indirizzo pubblico;
  il token resta segreto nei Secrets, non è visibile).
- Carica tutti i file di questa cartella, compresa `.github/workflows/pubblica.yml`.
- Settings → Actions → General → "Workflow permissions": seleziona **Read and write permissions**.

### 2. App Meta

1. Vai su <https://developers.facebook.com/apps/> ed entra col tuo account Facebook.
2. **Crea app**. Nella scelta dei casi d'uso seleziona **"Gestisci tutto sulla tua Pagina"**
   (Manage everything on your Page). Se richiesto, collega il portfolio aziendale che contiene la Pagina.
3. Nel caso d'uso della Pagina, sezione Autorizzazioni, aggiungi:
   `pages_manage_posts`, `pages_read_engagement`, `instagram_basic`, `instagram_content_publish`.
   (Se `instagram_*` non compare, aggiungi anche il caso d'uso Instagram con "accesso tramite Facebook Login".)
4. In Impostazioni app → Di base: compila **URL informativa sulla privacy** e
   **URL eliminazione dati** (va bene una pagina semplice del sito dell'associazione), poi
   **pubblica l'app (modalità Live)**.
   ⚠️ Importante: con l'app in modalità Sviluppo i post pubblicati sono visibili **solo a te**.
   Essendo tu amministratore dell'app e della Pagina, non serve la revisione di Meta.

### 3. Token della Pagina (non scade)

1. Apri lo **Graph API Explorer**: <https://developers.facebook.com/tools/explorer/>
2. In alto a destra scegli la tua app, "Token utente", aggiungi i permessi del punto 2.3
   più `pages_show_list` e `business_management`, poi **Generate Access Token**
   (nella finestra di consenso seleziona la Pagina e l'account Instagram).
3. Copia il token e aprilo nel **Debugger dei token**: <https://developers.facebook.com/tools/debug/accesstoken/>
   → pulsante **"Extend Access Token"** in fondo → copia il token esteso (dura 60 giorni).
4. Torna nell'Explorer, incolla il token esteso e fai la richiesta:
   `me/accounts?fields=id,name,access_token,instagram_business_account`
   Nella risposta, per la tua Pagina trovi:
   - `id` → è **FB_PAGE_ID**
   - `access_token` → è **META_PAGE_TOKEN** (questo non scade)
   - `instagram_business_account.id` → è **IG_USER_ID**
5. Verifica nel Debugger che il token della Pagina riporti "Scade: Mai".

### 4. Secrets su GitHub

Nel repository: Settings → Secrets and variables → Actions → **New repository secret**, crea:

| Nome | Valore |
|---|---|
| `META_PAGE_TOKEN` | token della Pagina |
| `FB_PAGE_ID` | ID della Pagina |
| `IG_USER_ID` | ID dell'account Instagram |

### 5. Prova

Scheda **Actions** → "Pubblica post" → **Run workflow**:
- `verifica`: controlla token, Pagina e Instagram collegato, senza pubblicare nulla;
- `prova`: mostra cosa verrebbe pubblicato adesso, senza pubblicare;
- `pubblica`: pubblica davvero i post arrivati all'orario (è quello che succede in automatico ogni 15 minuti).

Per un primo test reale: aggiungi un post con data/ora di qualche minuto fa e lancia `pubblica`.

## Note

- Un post in arretrato di più di 6 ore (es. GitHub fermo) viene saltato invece che pubblicato in ritardo.
- GitHub può sospendere le esecuzioni programmate di un repository inattivo da 60 giorni:
  se capita, basta riattivarle dalla scheda Actions (o aggiungere un post).
- Se un giorno perdi i diritti sulla Pagina, il token smette di funzionare: rifai il punto 3.
- Comandi locali (facoltativi): `pip install -r requirements.txt` poi `python social.py controlla`
  per verificare `post.yaml` e vedere il calendario.
