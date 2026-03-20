# iotsheltr

Stack Docker per:

- broker MQTT pubblico con autenticazione (`username/password`)
- interfaccia web per gestire più dispositivi Sheltr (`Sheltr Mini`, `Sheltr 4G / DR154`)
- configurazione schede protocollo 1.6 (`light`, `shutter`, `dimmer`, `thermostat`)
- assegnazione nomi canali e stanze
- interfaccia comando dispositivi (`luci`, `tapparelle`, `dimmer`, `termostato`) via MQTT
- controllo dispositivi raggruppato per stanze
- profili orari da interfaccia controllo (`luci`, `tapparelle`, `termostato`)
- pubblicazione configurazione su MQTT (retain) con `deviceType` e dispositivi associati

## Avvio rapido

```bash
cp .env.example .env
docker compose up -d --build
```

## Endpoint esposti

- Web UI controllo istanza: `http://<HOST>:8080/control/<istanza>`
- Web UI configurazione: `http://<HOST>:8080/config`
- MQTT TCP: `<HOST>:1883`
- MQTT WebSocket: `ws://<HOST>:9001`

## Credenziali MQTT (default)

- Username: `filippo`
- Password: `filippo1994`

Puoi cambiarle nel file `.env`:

```env
MQTT_USERNAME=filippo
MQTT_PASSWORD=filippo1994
```

## Uso della UI

1. Crea una nuova istanza/dispositivo Sheltr (es. `dr154-villa`).
2. Apri l'istanza dalla pagina config.
3. Se impostato in `.env`, fai login config (`CONFIG_AUTH_USERNAME` / `CONFIG_AUTH_PASSWORD`).
4. Seleziona il tipo dispositivo:
   - `Sheltr 4G / DR154`: profilo storico con configurazione attuale e payload `frame_*`.
   - `Sheltr Mini`: profilo Sheltr Cloud con payload `json`.
5. Il portale applica automaticamente il preset iniziale di topic, payload e scheda associata.
6. Aggiungi o modifica le schede (`light`, `shutter`, `dimmer`, `thermostat`).
7. Imposta indirizzo, range canali, nome canale e stanza.
8. Imposta login per istanza (`username` e `password`) nella pagina config.
9. Salva.
10. Premi `Pubblica su MQTT` per inviare la configurazione.
11. Apri il controllo dedicato su `/control/<istanza>`.
12. Se `Sheltr 4G / DR154` è in `transparent mode`, imposta `Formato payload luci` su un formato `frame_*`.
13. Per `Sheltr 4G / DR154` imposta anche `Topic risposta dispositivo` uguale al `Publish topic` del DR154.
14. La UI mostra lo stato ON/OFF confermato da polling protocollo (`command 0x40`) quando arriva risposta dal dispositivo.
15. In controllo puoi usare `Aggiorna` per fare polling immediato dispositivi e aggiornare le card.
16. In controllo trovi card stanza per `luci`, `tapparelle`, `dimmer`, `termostato`.
17. Sulle card con `⚙` puoi impostare il profilo orario.
18. In controllo non c'è polling automatico al refresh: il polling dispositivi parte solo con `Aggiorna`.

Topic di default per la configurazione:

```text
dr154/<istanza>/config
```

Messaggio pubblicato in JSON, con `retain=true`.
Il payload include anche:

- `deviceType`: profilo selezionato (`sheltr_mini` oppure `sheltr_4g`)
- `device`: metadati del profilo dispositivo
- `devices`: elenco piatto dei dispositivi/canali associati pubblicati

Topic default comandi luci:

```text
dr154/<istanza>/cmd/light
```

Topic default risposta dispositivo (`Sheltr 4G / DR154`):

```text
dr154/<istanza>/pub/light
```

Configurazione DR154 consigliata:

- `Subscriber topic` (DR154): `dr154/<istanza>/cmd/light`
- `Publish topic` (DR154): `dr154/<istanza>/pub/light`

Nota: non usare la slash iniziale (`/`).  
`dr154/casa-demo/cmd/light` e `/dr154/casa-demo/cmd/light` sono due topic diversi.

Formati payload supportati per i comandi dispositivo:

- `frame_hex_space`: es. `49 01 51 41 00 00 00 00 00 00 00 00 00 46`
- `frame_hex_compact`: es. `4901514100000000000000000046`
- `frame_hex_space_crlf` (consigliato per DR154 transparent): come sopra + terminatore `\r\n`
- `frame_hex_compact_crlf`: come sopra + terminatore `\r\n`
- `frame_bytes`: invio bytes raw del frame protocollo
- `json`: payload JSON applicativo

## Affidabilita comandi luce

Per ridurre i comandi persi in rete:

- `MQTT_COMMAND_QOS=1` (default)
- retry publish automatico lato web app:
  - `MQTT_COMMAND_RETRIES=2`
  - `MQTT_COMMAND_RETRY_DELAY_MS=180`
- per payload `frame_*` su azioni `on/off`, invio ripetuto (idempotente):
  - `MQTT_COMMAND_REPEAT_ONOFF=2`
  - `MQTT_COMMAND_REPEAT_GAP_MS=120`
- conferma stato via risposta dispositivo (polling `0x40`):
  - `MQTT_RESPONSE_TIMEOUT_MS=2600`
  - `MQTT_RESPONSE_RETRIES=2`
  - `MQTT_RESPONSE_RETRY_DELAY_MS=220`
  - `MQTT_RESPONSE_AFTER_COMMAND_DELAY_MS=320`
  - tuning dedicato termostato:
    - `THERMOSTAT_RESPONSE_TIMEOUT_MS=4500`
    - `THERMOSTAT_RESPONSE_RETRIES=3`
    - `THERMOSTAT_RESPONSE_RETRY_DELAY_MS=400`
    - `THERMOSTAT_RESPONSE_AFTER_COMMAND_DELAY_MS=700`
    - `THERMOSTAT_COMMAND_FRAME_GAP_MS=220`
  - `MQTT_REQUIRE_RESPONSE=false` (se `true`, il comando fallisce senza conferma)
- loop profili orari luci:
  - `LIGHT_PROFILE_LOOP_INTERVAL_SEC=20`
- sessione login per istanza:
  - `INSTANCE_AUTH_TTL_SEC=43200`
  - `INSTANCE_AUTH_SECRET=...` (consigliato in produzione, stringa lunga casuale)
- login schermata config (opzionale):
  - `CONFIG_AUTH_USERNAME=...`
  - `CONFIG_AUTH_PASSWORD=...`
  - `CONFIG_AUTH_TTL_SEC=43200`

Azioni supportate:

- `light`: `on`, `off`
- `shutter`: `up`, `down`, `stop`
- `dimmer`: `on`, `off`, `toggle`, `set(level 0..9)`
- `thermostat`: `setpoint`, `mode(winter/summer)`, `power(on/off)`

## Note esposizione Internet

La compose espone direttamente le porte; per produzione Internet è consigliato:

- usare firewall (`1883`, `9001`, `8080` solo se necessario)
- mettere la UI dietro reverse proxy HTTPS
- usare password robuste (non quelle di default)
