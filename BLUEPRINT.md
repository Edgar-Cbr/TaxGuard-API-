# 🚀 TaxGuard API — B2B Data & Fiscal Validation Micro-SaaS
> **Documento de Contexto y Hoja de Ruta para Desarrollo Asistido (Antigravity Blueprint)**

---

## 1. Identidad del Repositorio en GitHub

Para que el repositorio luzca como un producto de ingeniería real desde la vista previa de tu perfil, utiliza esta configuración al crearlo en GitHub:

* **Nombre principal recomendado:** `taxguard-api`
  *(Alternativas atractivas: `veriflow-engine`, `kyb-shield-api`, `dataguard-saas`)*
* **Descripción corta (About):** 
  `⚡ High-performance B2B Micro-SaaS API for fiscal ID (RFC/TIN), postal address & corporate email validation. Built with FastAPI, Async SQLAlchemy, Redis (Sliding Window Rate Limiting) & Stripe Webhooks.`
* **Topics (Etiquetas de GitHub):** 
  `fastapi` `python` `micro-saas` `redis` `postgresql` `rate-limiting` `clean-architecture` `sqlalchemy-async` `docker` `rest-api`

---

## 2. Contexto del Sistema y Reglas para el Agente (Antigravity Rules)

Este proyecto es un **API Micro-SaaS B2B** diseñado para validar datos críticos de onboarding empresarial (identificadores fiscales como RFC/TIN, códigos postales y dominios corporativos). 

### Reglas Estrictas de Ingeniería:
1. **Stack Tecnológico:** Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2.0 (100% asíncrono con `asyncpg`), Alembic para migraciones, Redis (`redis.asyncio`) y Pytest.
2. **Arquitectura Desacoplada:** Separar estrictamente las rutas HTTP (`api/`), esquemas de validación (`schemas/`), lógica de negocio pura (`services/`), modelos de persistencia (`models/`) y configuración (`core/`).
3. **Seguridad de API Keys:** Nunca guardar ni registrar una API Key en texto plano. Las llaves tienen el formato `tg_live_<32_hex_chars>`. Solo se almacena en base de datos su hash `SHA-256` y los primeros 10 caracteres como `key_prefix`.
4. **Rendimiento y Latencia:** Las validaciones de cuota y *Rate Limiting* deben resolverse en Redis con complejidad $O(1)$ antes de tocar PostgreSQL. El registro de logs de consumo (`usage_logs`) debe ejecutarse de forma asíncrona en segundo plano (`BackgroundTasks` o worker).
5. **Manejo de Errores:** Todas las excepciones deben devolver un formato JSON estandarizado (inspirado en RFC 7807) con `error_code`, `message`, `status_code` y `request_id`.

---

## 3. Estructura del Árbol de Directorios

```text
taxguard-api/
├── .github/
│   └── workflows/
│       └── ci.yml                 # Linter (Ruff) + Tests (Pytest) automatizados
├── app/
│   ├── api/
│   │   ├── deps.py                # Inyección de dependencias (DB, Redis, Auth JWT y X-API-Key)
│   │   └── v1/
│   │       ├── endpoints/
│   │       │   ├── auth.py        # Registro, login y emisión de JWT
│   │       │   ├── keys.py        # Creación, listado, revocación y métricas de API Keys
│   │       │   ├── validators.py  # Endpoints core del SaaS (Tax ID, Address, Email, Batch)
│   │       │   └── billing.py     # Simulación de Webhook de Stripe para upgrade de plan
│   │       └── router.py          # Enrutador principal v1
│   ├── core/
│   │   ├── config.py              # Variables de entorno con Pydantic Settings
│   │   ├── security.py            # Hashing SHA-256, Bcrypt/Argon2 y generación de JWT
│   │   ├── rate_limit.py          # Algoritmo Sliding Window y control de cuotas en Redis
│   │   └── exceptions.py          # Manejadores globales de excepciones HTTP
│   ├── db/
│   │   ├── base.py                # DeclarativeBase de SQLAlchemy 2.0
│   │   ├── session.py             # AsyncEngine y generador de AsyncSession
│   │   └── migrations/            # Entorno de Alembic
│   ├── models/
│   │   ├── user.py                # Modelo User y Subscription
│   │   ├── api_key.py             # Modelo ApiKey
│   │   └── usage_log.py           # Modelo UsageLog
│   ├── schemas/
│   │   ├── auth.py                # DTOs de usuario y tokens
│   │   ├── api_key.py             # DTOs de creación y respuesta de llaves
│   │   └── validator.py           # DTOs de entrada/salida para validaciones B2B
│   ├── services/
│   │   ├── tax_validator.py       # Reglas sintácticas, fecha y dígito verificador (RFC/TIN)
│   │   ├── address_validator.py   # Cruce de códigos postales con caché en Redis
│   │   ├── email_validator.py     # Verificación de sintaxis, dominios desechables y registros MX
│   │   └── usage_tracker.py       # Escritura asíncrona de auditoría en PostgreSQL
│   └── main.py                    # Punto de entrada FastAPI, CORS, Lifespan y Healthcheck
├── tests/
│   ├── conftest.py                # Fixtures asíncronos (AsyncClient, DB de prueba, Redis mock)
│   ├── test_auth_and_keys.py      # Pruebas de ciclo de vida de llaves y seguridad
│   ├── test_rate_limiting.py      # Pruebas de bloqueo 429 Too Many Requests
│   └── test_validators.py         # Pruebas unitarias y de integración del motor validador
├── .env.example                   # Plantilla de variables de entorno
├── Dockerfile                     # Construcción multi-stage ligera
├── docker-compose.yml             # Orquestación local: API + PostgreSQL 16 + Redis 7
├── pyproject.toml                 # Dependencias, configuración de Ruff y Pytest
└── README.md                      # Documentación de alto impacto para el portafolio
```

---

## 4. Diseño de Base de Datos y Claves de Redis

### 4.1 Tablas en PostgreSQL
* **`users`**:
  * `id` (UUID, PK), `email` (VARCHAR, Unique, Index), `hashed_password` (VARCHAR), `tier` (ENUM: `FREE`, `PRO`, `ENTERPRISE`), `is_active` (BOOL), `created_at` (TIMESTAMPTZ).
* **`subscriptions`**:
  * `id` (UUID, PK), `user_id` (UUID, FK `users.id`, Unique), `stripe_customer_id` (VARCHAR), `monthly_quota` (INT: 1000 para Free, 50000 para Pro), `rate_limit_per_sec` (INT: 5 para Free, 50 para Pro), `current_period_end` (TIMESTAMPTZ).
* **`api_keys`**:
  * `id` (UUID, PK), `user_id` (UUID, FK `users.id`), `name` (VARCHAR), `key_prefix` (VARCHAR(14)), `key_hash` (CHAR(64), Unique Index), `is_active` (BOOL), `revoked_at` (TIMESTAMPTZ, Nullable), `last_used_at` (TIMESTAMPTZ, Nullable), `created_at` (TIMESTAMPTZ).
* **`usage_logs`**:
  * `id` (BIGSERIAL, PK), `api_key_id` (UUID, FK `api_keys.id`, Index), `endpoint` (VARCHAR), `status_code` (SMALLINT), `latency_ms` (FLOAT), `created_at` (TIMESTAMPTZ, Index).

### 4.2 Estructura de Claves en Redis
* **Caché de Autenticación de API Key (TTL 5 min):**
  `apikey:meta:{key_hash}` $\rightarrow$ JSON con `{api_key_id, user_id, tier, monthly_quota, rate_limit_per_sec, is_active}`.
* **Rate Limiting por Segundo (TTL 1 seg):**
  `ratelimit:{api_key_id}:{epoch_second}` $\rightarrow$ Contador entero atómico (`INCR`).
* **Cuota Mensual Acumulada (TTL 35 días):**
  `quota:{user_id}:{YYYY-MM}` $\rightarrow$ Contador entero atómico de llamadas consumidas en el mes.
* **Caché de Catálogo de Códigos Postales (TTL 24 horas):**
  `catalog:zip:{country_code}:{postal_code}` $\rightarrow$ JSON con estado, municipio y lista de colonias/asentamientos.

---

## 5. Plan de Ejecución Paso a Paso (Prompts para Antigravity)

Ejecuta este proyecto en **7 fases incrementales**. Pídele a Antigravity que complete y verifique una fase antes de pasar a la siguiente:

### Fase 1: Cimientos, Configuración y Dockerización
* [ ] Crear `pyproject.toml` con las dependencias: `fastapi`, `uvicorn[standard]`, `sqlalchemy[asyncio]`, `asyncpg`, `alembic`, `pydantic-settings`, `redis`, `passlib[bcrypt]`, `python-jose[cryptography]`, `dnspython`, `httpx`, `pytest`, `pytest-asyncio`, `ruff`.
* [ ] Configurar `app/core/config.py` leyendo variables de `.env` (`DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, `ENVIRONMENT`).
* [ ] Crear `Dockerfile` y `docker-compose.yml` con servicios para `api`, `db` (PostgreSQL 16) y `redis` (Redis 7 Alpine) con *healthchecks*.
* [ ] Inicializar `app/main.py` con el evento `lifespan` que verifique la conexión a PostgreSQL y Redis en `/health`.

### Fase 2: Modelos de Datos y Migraciones con Alembic
* [ ] Implementar `app/db/base.py` y `app/db/session.py` con `create_async_engine` y `async_sessionmaker`.
* [ ] Programar los 4 modelos en `app/models/` (`User`, `Subscription`, `ApiKey`, `UsageLog`).
* [ ] Configurar Alembic en modo asíncrono y generar la primera migración (`alembic revision --autogenerate -m "initial_schema"`).

### Fase 3: Autenticación JWT y Motor Criptográfico de API Keys
* [ ] Implementar en `app/core/security.py`:
  * Función para generar API Keys con `secrets.token_hex(16)` y prefijo `tg_live_`.
  * Función para obtener el hash `SHA-256` de cualquier llave entrante.
* [ ] Crear endpoints en `app/api/v1/endpoints/auth.py` (`POST /register` que cree el usuario y su suscripción `FREE` por defecto, y `POST /login` que devuelva el JWT).
* [ ] Crear endpoints en `app/api/v1/endpoints/keys.py`:
  * `POST /keys`: Genera una llave, guarda el hash en DB y devuelve la llave en texto plano **solo en esta respuesta**.
  * `GET /keys`: Lista las llaves del usuario mostrando únicamente `key_prefix`, `name`, `created_at` y `last_used_at`.
  * `DELETE /keys/{key_id}`: Revoca una llave e invalida su caché en Redis inmediatamente.

### Fase 4: Middleware de Rate Limiting y Control de Cuotas en Redis
* [ ] Construir la dependencia `verify_api_key_and_quota` en `app/api/deps.py` y `app/core/rate_limit.py`:
  1. Extraer header `X-API-Key`. Si no existe, retornar `401 Unauthorized`.
  2. Calcular `SHA-256` y buscar metadatos en Redis (`apikey:meta:{hash}`). Si no está en Redis, consultar PostgreSQL y guardar en caché por 300 segundos.
  3. Verificar el contador por segundo (`ratelimit:{api_key_id}:{sec}`) y el contador mensual (`quota:{user_id}:{YYYY-MM}`) mediante un *pipeline* de Redis.
  4. Si excede el límite por segundo o la cuota mensual, lanzar `429 Too Many Requests` detallando el tiempo de espera (`Retry-After`).
  5. Inyectar en el objeto `Response` los encabezados: `X-RateLimit-Limit`, `X-RateLimit-Remaining` y `X-Quota-Remaining`.

### Fase 5: Servicios del Dominio de Validación B2B
* [ ] **`POST /api/v1/validate/tax-id` (`tax_validator.py`):**
  * Validar longitud (12 caracteres para persona moral, 13 para persona física en formato RFC mexicano, o soporte configurable para TIN).
  * Validar que los 6 dígitos centrales correspondan a una fecha de nacimiento/constitución real en el calendario.
  * Calcular y verificar el dígito verificador oficial (Módulo 11).
  * Retornar JSON con `valid`, `entity_type`, `normalized_id`, `registration_date` y `checksum_valid`.
* [ ] **`POST /api/v1/validate/postal-code` (`address_validator.py`):**
  * Consultar en Redis o catálogo base en memoria/JSON el código postal, devolviendo estado, municipio, ciudad y asentamientos válidos con latencia menor a $10\text{ ms}$.
* [ ] **`POST /api/v1/validate/corporate-email` (`email_validator.py`):**
  * Verificar sintaxis, comprobar si el dominio pertenece a una lista de proveedores gratuitos/desechables (ej. `mailinator.com`, `tempmail.com`, `gmail.com`) y verificar la existencia de registros `MX` reales mediante `dnspython`.

### Fase 6: Auditoría Asíncrona, Métricas y Webhook de Stripe
* [ ] Crear `app/services/usage_tracker.py` para que, tras cada validación exitosa en los endpoints B2B, una tarea en segundo plano (`BackgroundTasks`) inserte el registro en `usage_logs` (con el tiempo de ejecución en milisegundos) y actualice `last_used_at` en `api_keys`.
* [ ] Crear `GET /api/v1/keys/usage` para devolver estadísticas agregadas del usuario: llamadas totales del mes, desglose por código de estado (`200`, `422`, `429`) y latencia promedio en milisegundos.
* [ ] Crear `POST /api/v1/billing/webhook` que reciba un payload simulado de Stripe (`checkout.session.completed`), actualice el `tier` del usuario a `PRO` (50,000 créditos/mes y 50 req/seg) y limpie la caché de sus llaves en Redis.

### Fase 7: Suite de Pruebas, CI/CD y Pulido de Portafolio
* [ ] Escribir pruebas automatizadas en `tests/` cubriendo:
  * Creación de cuenta, generación de API Key y rechazo de llave revocada (`401`).
  * Disparo de `429 Too Many Requests` al superar el umbral de peticiones por segundo.
  * Casos válidos e inválidos del validador fiscal (fechas imposibles como `30 de febrero`, homoclave mal formada).
* [ ] Crear `.github/workflows/ci.yml` para ejecutar `ruff check .` y `pytest` automáticamente en cada `push`.
* [ ] Redactar un `README.md` ejecutivo con diagrama de arquitectura Mermaid, guía de inicio rápido con `docker compose up --build` y ejemplos de peticiones `curl` mostrando los headers `X-RateLimit`.