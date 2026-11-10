# TV Italia — addon Stremio

Tutta la TV italiana **gratuita** in diretta su Stremio: Rai, Mediaset, La7, canali
nazionali, sport in chiaro, news, bambini, locali e canali FAST.

- **Fonte**: database pubblico [iptv-org](https://github.com/iptv-org/api), aggiornato
  automaticamente ogni 6 ore. Nessun link scritto a mano da mantenere.
- **Filtri**: solo canali registrati in Italia, ancora in onda, non per adulti, con almeno
  una sorgente. I marchi pay‑TV (Sky, DAZN, Eurosport…) sono esclusi; Sky TG24, che è
  gratuito sul digitale terrestre, resta.
- **Ordinamento**: come il telecomando (numerazione LCN del digitale terrestre), poi gli altri
  in ordine alfabetico.
- **Generi**: Generalisti, Sport, Informazione, Cinema, Bambini, Documentari, ecc. Si filtra
  dal menu genere di Stremio, e c'è anche la ricerca.
- **Più sorgenti per canale**, dalla qualità più alta; Referer/User‑Agent passati al player
  quando servono.

## Avvio locale

```bash
cd tvitalia
pip install -r requirements.txt
python3 app.py
```

Apri `http://localhost:8080` e premi **Installa su Stremio**.

## Deploy gratuito su Render

Il servizio `tv-italia` è già in `render.yaml`: su Render scegli *New → Blueprint*, collega
il repository e conferma. Al termine apri l'URL del servizio e installa l'addon da lì.

Variabili opzionali: `ADDON_NAME`, `REFRESH_HOURS` (default 6), `LOG_LEVEL`, `PORT`.

## Test

```bash
pip install pytest
python3 -m pytest tvitalia/tests
```

Nota: alcune emittenti limitano lo streaming all'Italia (geo‑blocco) o cambiano spesso
gli indirizzi; se un canale non parte, prova un'altra sorgente nell'elenco.
