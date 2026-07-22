# Puriq — Guía de configuración (qué instalar y qué credenciales obtener)

Ordenado por cuándo lo necesitas. **Lo marcado con ⏰ pídelo YA** porque la aprobación tarda.

## 1. Entorno local (Día 2–3, sin cuentas externas)

| Herramienta | Para qué | Cómo |
|---|---|---|
| **Node ≥ 18** (tienes v22) + npm | Plantilla Astro | ya instalado |
| **Python ≥ 3.10** | Agente | `python --version` |
| **Git** + cuenta **GitHub** | Repo público (¡entregable!) | crear repo `puriq` público |
| **Claude Code** o **Kiro** | IDE de desarrollo | ver recomendación en PROYECTO |

Instalación del proyecto:

```bash
# Plantilla
cd template && npm install && npm run build     # o: npm run dev

# Agente
cd agent && python -m venv .venv && source .venv/bin/activate
pip install -e ".[local,mcp]"
```

## 2. GitHub (Día 1)

- Crear **repositorio público** `puriq` (es entregable obligatorio + README).
- `git init && git add . && git commit -m "scaffolding" && git remote add origin ... && git push`.

## 3. AWS (Día 4–5, pero ⏰ configurar el acceso HOY)

### 3.1 Cuenta y credenciales
- **Cuenta AWS** (espera los **créditos del hackathon** antes de gastar; la cuenta puedes crearla ya).
- **IAM**: crea un usuario (o IAM Identity Center) con permisos para Bedrock, S3 y Amplify. Genera **Access Key + Secret**.
- Configura la CLI: `aws configure` → región **`us-east-1`** (mejor disponibilidad de modelos Bedrock).

### 3.2 ⏰ Amazon Bedrock — acceso a modelos (¡lo primero!)
- Consola **Bedrock → Model access → Manage model access** → solicita **Anthropic Claude** (y **Titan Embeddings** si harás RAG con Bedrock).
- La aprobación puede tardar horas. **Sin esto, el agente no genera contenido.** Hazlo hoy.
- El agente lee el modelo de `PURIQ_BEDROCK_MODEL` (ver `.env.example`).

### 3.3 RAG del chatbot (Día 4) — decisión de alcance
- **MVP recomendado (barato):** embeddings con **Amazon Titan (Bedrock)** + vector store **local** (FAISS/sqlite-vec). Usa AWS (suma puntos) sin costos altos.
- **Camino AWS completo (más caro):** **Bedrock Knowledge Bases** → requiere un bucket **S3** + **OpenSearch Serverless** (este último tiene costo mínimo continuo). Menciónalo como evolución; no lo necesitas para el MVP.

### 3.4 Deploy (Día 5–6) — elige un adaptador
- **AWS Amplify Hosting (recomendado):** conecta el repo de GitHub, build automático, HTTPS y dominio. Cero servidores.
- **S3 + CloudFront:** sube `dist/` a un bucket S3 + distribución CloudFront.
- **Export estático:** `dist/` listo para cualquier hosting (plan B sin AWS).

### 3.5 Opcional — Amazon Location Service
- Solo si usas geocoding/tiles vía AWS: crea un **Place Index**. Si no, el agente usa Nominatim (OSM) gratis.

## 4. Kiro (cuando carguen los créditos)
- Instala Kiro y úsalo para una porción real del desarrollo (un módulo o el pipeline del agente) → cubre el 10% "Uso de AWS y Kiro" y da el meta-relato para el video. Se puede combinar con otros IDEs.

## 5. Secretos y variables de entorno
- Copia `agent/.env.example` a `agent/.env` y complétalo. **`.env` está en `.gitignore`** (nunca lo subas).
- Nunca pongas claves en el código ni en los JSON del contrato.

## Resumen: qué cuentas/claves obtener
- **GitHub** — repo público (gratis).
- **AWS** — cuenta + IAM Access Key/Secret + **acceso a modelos Bedrock** (⏰) + (opcional) bucket S3 / app Amplify.
- **Kiro** — licencia/créditos (pendiente de carga).
- Sin terceros de pago: Leaflet/OpenStreetMap y Nominatim son gratis.

## Nota de costos
- **Bedrock**: pago por uso, texto es barato para un demo.
- **Amplify / S3+CloudFront**: free tier / centavos.
- **OpenSearch Serverless** (solo si usas Knowledge Bases): tiene costo mínimo continuo → evítalo en el MVP.
- Los **créditos AWS del hackathon** deberían cubrir todo esto.
