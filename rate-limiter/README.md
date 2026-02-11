# rate-limiter

Окремий сервіс rate limiting (FastAPI + Redis):
- **Token bucket** (атомарно через Lua)
- **Concurrency leases** (ZSET + TTL) — для “важких” задач, де важливіше обмежити одночасні обробки, ніж RPS

## Швидкий старт (docker-compose)

```bash
cp .env.example .env
cp policies.example.json policies.json
docker compose up --build
```

Сервіс підніметься на `http://localhost:8080`.

## Авторизація

Якщо `AUTH_TOKEN` заданий — додавай хедер:

`X-RL-Auth: <AUTH_TOKEN>`

Якщо `AUTH_TOKEN` пустий — сервіс відкритий.

## API

- `GET  /healthz`
- `POST /v1/allow`
- `POST /v1/lease/acquire`
- `POST /v1/lease/release`

### POST /v1/allow

Перевіряє token-bucket і (опційно) робить concurrency-check за політикою.

```bash
curl -X POST http://localhost:8080/v1/allow \
  -H 'Content-Type: application/json' \
  -H 'X-RL-Auth: change-me' \
  -d '{"key":"svc:video-worker","method":"PUT","path":"/proxy/bucket/x","cost":1}'
```

Відповідь: `allowed=true/false`, підказки `retry_after_ms`, `reset_after_ms`, `remaining_tokens`.

> Нюанс: якщо в policy є concurrency, `/v1/allow` спочатку “спише” токен, а потім спробує взяти слот.  
> Якщо тобі треба **строга** модель “спочатку слот, потім робота” — юзай lease-ендпоїнти.

### POST /v1/lease/acquire + /v1/lease/release

Для суперважких задач (CPU/GPU, платні провайдери):
1) `acquire`
2) робота
3) `release` у `finally`

Якщо робота помре — TTL автоматично “відпустить” слот.

## Політики

Дивись `policies.json`:
- `default` — дефолтна політика
- `rules` — матч по `methods` + `path_prefix`
- `scope`:
  - `key` — ліміт на ключ (API key / service id)
  - `key_route` — ліміт на ключ + маршрут (method+path)

`bypass_keys` — ключі, які не лімітяться.

## Ключі (key)

Якщо `key` не передали в JSON, сервіс пробує:
- `X-Api-Key`
- `X-Service-Id`
- інакше (якщо `FALLBACK_TO_IP=true`) — `ip:<client_ip>`
