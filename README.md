# iotsheltr

Stack Docker per:

- broker MQTT pubblico con autenticazione (`username/password`)
- interfaccia web per gestire più istanze DR154
- configurazione schede protocollo 1.6 (`light`, `shutter`, `dimmer`, `thermostat`)
- assegnazione nomi canali e stanze
- interfaccia comando luci (`ON/OFF/TOGGLE`) via MQTT
- pubblicazione configurazione su MQTT (retain)

## Avvio rapido

```bash
cp .env.example .env
docker compose up -d --build
```

## Endpoint esposti

- Web UI: `http://<HOST>:8080`
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
2. Apri l'istanza.
3. Aggiungi le schede (`light`, `shutter`, `dimmer`, `thermostat`).
4. Imposta indirizzo, range canali, nome canale e stanza.
5. Salva.
6. Premi `Pubblica su MQTT` per inviare la configurazione.
7. Usa `Controllo Luci` per inviare comandi realtime ai canali luce.
8. Se DR154 è in `transparent mode`, imposta `Formato payload luci` su un formato `frame_*`.

Topic di default per la configurazione:

```text
dr154/<istanza>/config
```

Messaggio pubblicato in JSON, con `retain=true`.

Topic default comandi luci:

```text
dr154/<istanza>/cmd/light
```

Formati payload supportati per i comandi luce:

- `frame_hex_space` (default): es. `49 01 51 41 00 00 00 00 00 00 00 00 00 46`
- `frame_hex_compact`: es. `4901514100000000000000000046`
- `frame_bytes`: invio bytes raw del frame protocollo
- `json`: payload JSON applicativo

## Note esposizione Internet

La compose espone direttamente le porte; per produzione Internet è consigliato:

- usare firewall (`1883`, `9001`, `8080` solo se necessario)
- mettere la UI dietro reverse proxy HTTPS
- usare password robuste (non quelle di default)
