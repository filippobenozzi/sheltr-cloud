# iotsheltr

Stack Docker per:

- broker MQTT pubblico con autenticazione (`username/password`)
- interfaccia web React + `shadcn/ui` per gestire più dispositivi Sheltr (`Sheltr Mini`, `Sheltr 4G / DR154`)
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

La build Docker della webapp compila automaticamente il frontend Vite e pubblica la SPA sotto `webapp/static/app`.
Se stai eseguendo solo Flask senza build frontend, il backend mantiene un fallback alle vecchie pagine statiche.

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
   - `Sheltr Mini`: profilo Sheltr Cloud standard del firmware Sheltr Mini.
5. Il portale applica automaticamente il preset iniziale di topic, payload e scheda associata.
   - Per `Sheltr Mini` la configurazione cloud e standard:
     - `instanceId` = ID istanza
     - `configTopic` = `<istanza>/config`
     - `commandTopic` = `<istanza>/cmd`
     - `responseTopic` = `<istanza>/pub`
     - `payloadFormat` = `frame_hex_space_crlf`
   - Per `Sheltr Mini` questi campi non si configurano manualmente nel portale.
   - Per `Sheltr 4G / DR154` i topic non si configurano manualmente nel portale.
     - `configTopic` = `/<istanza>/config`
     - `commandTopic` = `/<istanza>/cmd`
     - `responseTopic` = `/<istanza>/status`
     - `payloadFormat` = `frame_hex_space_crlf`
   - Per `Sheltr Mini` non configuri manualmente le schede nel portale: il profilo si sincronizza dai dispositivi pubblicati dal Mini sul topic retained `<istanza>/config`.
6. Aggiungi o modifica le schede (`light`, `shutter`, `dimmer`, `thermostat`).
   - Questo passaggio vale per `Sheltr 4G / DR154`.
7. Imposta indirizzo, range canali, nome canale e stanza.
8. Imposta login per istanza (`username` e `password`) nella pagina config.
9. Salva.
10. Premi `Pubblica su MQTT`.
    - Per `Sheltr 4G / DR154` invia la configurazione su MQTT.
    - Per `Sheltr Mini` la UI mostra `Sincronizza Sheltr Mini` ed esegue la sincronizzazione dal topic retained `<istanza>/config` pubblicato dal Mini.
11. Apri il controllo dedicato su `/control/<istanza>`.
12. Per `Sheltr 4G / DR154` configura il modulo con:
    - `Subscriber topic`: `/<istanza>/cmd`
    - `Publish topic`: `/<istanza>/status`
13. La UI mostra lo stato ON/OFF confermato da polling protocollo (`command 0x40`) quando arriva risposta dal dispositivo.
14. In controllo puoi usare `Aggiorna` per fare polling immediato dispositivi e aggiornare le card.
15. In controllo trovi card stanza per `luci`, `tapparelle`, `dimmer`, `termostato`.
16. Sulle card con `⚙` puoi impostare il profilo orario.
17. In controllo non c'è polling automatico al refresh: il polling dispositivi parte solo con `Aggiorna`.

Topic di default per la configurazione `Sheltr 4G / DR154`:

```text
/<istanza>/config
```

Messaggio pubblicato in JSON, con `retain=true`.
Il payload include anche:

- `deviceType`: profilo selezionato (`sheltr_mini` oppure `sheltr_4g`)
- `device`: metadati del profilo dispositivo
- `devices`: elenco piatto dei dispositivi/canali associati pubblicati

Topic default comandi dispositivo (`Sheltr 4G / DR154`):

```text
/<istanza>/cmd
```

Topic default risposta dispositivo (`Sheltr 4G / DR154`):

```text
/<istanza>/status
```

Configurazione DR154 consigliata:

- `Subscriber topic` (DR154): `/<istanza>/cmd`
- `Publish topic` (DR154): `/<istanza>/status`

Per `Sheltr Mini` con istanza `casa-pizero`:

- `Topic configurazione`: `casa-pizero/config`
- `Topic comandi dispositivo`: `casa-pizero/cmd`
- `Topic risposta dispositivo`: `casa-pizero/pub`
- `Formato payload comandi`: `frame_hex_space_crlf`

Nota: per `Sheltr 4G / DR154` la slash iniziale fa parte del topic.  
`/casa-demo/cmd` e `casa-demo/cmd` sono due topic diversi.

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
  - `MQTT_REQUIRE_RESPONSE=false` (se `true`, il portale prova a confermare il comando via polling `0x40`)
  - `MQTT_STRICT_RESPONSE=false` (se `true`, il comando fallisce senza conferma; se `false`, torna `ok` ma con `verified=false` e `verifyReason`)
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
