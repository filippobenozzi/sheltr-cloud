# iotsheltr

Stack Docker per:

- broker MQTT pubblico con autenticazione (`username/password`)
- interfaccia web per gestire più istanze DR154
- configurazione schede protocollo 1.6 (`light`, `shutter`, `dimmer`, `thermostat`)
- assegnazione nomi canali e stanze
- interfaccia comando luci (`ON/OFF`) via MQTT
- controllo luci raggruppato per stanze
- profili orari luci (fasce orarie + giorni) da interfaccia controllo
- pubblicazione configurazione su MQTT (retain)

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

1. Crea una nuova istanza DR154 (es. `dr154-villa`).
2. Apri l'istanza dalla pagina config.
3. Aggiungi le schede (`light`, `shutter`, `dimmer`, `thermostat`).
4. Imposta indirizzo, range canali, nome canale e stanza.
5. Imposta login per istanza (`username` e `password`) nella pagina config.
6. Salva.
7. Premi `Pubblica su MQTT` per inviare la configurazione.
8. Apri il controllo dedicato su `/control/<istanza>`.
9. Se DR154 è in `transparent mode`, imposta `Formato payload luci` su un formato `frame_*`.
10. Imposta anche `Topic risposta DR154` uguale al `Publish topic` del DR154.
11. La UI mostra lo stato ON/OFF confermato da polling protocollo (`command 0x40`) quando arriva risposta dal dispositivo.
12. In controllo puoi usare `Aggiorna` per fare polling immediato dispositivi e aggiornare le card.
13. Su ogni card luce puoi usare `⚙` per impostare il profilo orario.

Topic di default per la configurazione:

```text
dr154/<istanza>/config
```

Messaggio pubblicato in JSON, con `retain=true`.

Topic default comandi luci:

```text
dr154/<istanza>/cmd/light
```

Topic default risposta DR154:

```text
dr154/<istanza>/pub/light
```

Configurazione DR154 consigliata:

- `Subscriber topic` (DR154): `dr154/<istanza>/cmd/light`
- `Publish topic` (DR154): `dr154/<istanza>/pub/light`

Nota: non usare la slash iniziale (`/`).  
`dr154/casa-demo/cmd/light` e `/dr154/casa-demo/cmd/light` sono due topic diversi.

Formati payload supportati per i comandi luce:

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
  - `MQTT_RESPONSE_TIMEOUT_MS=1600`
  - `MQTT_RESPONSE_RETRIES=1`
  - `MQTT_RESPONSE_RETRY_DELAY_MS=140`
  - `MQTT_REQUIRE_RESPONSE=false` (se `true`, il comando fallisce senza conferma)
- loop profili orari luci:
  - `LIGHT_PROFILE_LOOP_INTERVAL_SEC=20`
- sessione login per istanza:
  - `INSTANCE_AUTH_TTL_SEC=43200`
  - `INSTANCE_AUTH_SECRET=...` (consigliato in produzione, stringa lunga casuale)

Nota: le azioni supportate per le luci sono `on` e `off`.

## Note esposizione Internet

La compose espone direttamente le porte; per produzione Internet è consigliato:

- usare firewall (`1883`, `9001`, `8080` solo se necessario)
- mettere la UI dietro reverse proxy HTTPS
- usare password robuste (non quelle di default)
