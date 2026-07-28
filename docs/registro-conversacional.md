# Registro conversacional

En vez de completar formularios, el usuario **conversa** y el agente registra. Es
la vía pensada para quien no va a aprender una interfaz: cuenta lo que tiene, y
el sitio se arma.

Para conectar tu cliente, ver [mcp-clientes.md](mcp-clientes.md). Para la
arquitectura general, [ARQUITECTURA.md](ARQUITECTURA.md).

## Un núcleo, dos superficies

La lógica vive en `puriq/intake/tools.py` **sin transporte**: recibe argumentos,
valida, persiste y devuelve el estado. No sabe de HTTP ni de stdio.

```
                    ┌─────────────────────────┐
   Claude Desktop   │                         │
   Kiro, Cline   ──►│  intake tools           │──►  CONTRATO (3 JSON)
   (su modelo)      │  · 14 funciones         │
                    │  · el guion por fases   │
   Chat del      ──►│  · valida y persiste    │
   wizard           │                         │
   (modelo propio)  └─────────────────────────┘
```

Las dos superficies llaman a las mismas funciones, así que el agente se comporta
igual en ambas. La diferencia es **quién pone el modelo**: por MCP lo pone el
cliente —sin credenciales de por medio—; en el chat del wizard, Puriq.

## El guion por fases

`INTAKE_GUION` es la fuente única: se sirve como recurso MCP (`intake://guion`),
como tool (`get_guion`, para clientes que no leen recursos) y embebido en el
prompt del chat web.

```
Fase 0  Qué es       ¿un destino o un emprendimiento? cambia todo lo demás
Fase 1  Sitio        nombre, región, centro del mapa, idioma, contacto
Fase 2  Módulos      "quiero lugares y eventos" → activa places + events
Fase 3  Lugares      nombre y categoría bastan; PIDE fotos
Fase 4  Eventos      fechas, lugar asociado
Fase 5  Marca        propone una paleta; PIDE el logo
Fase 6  Portada      arma la landing según lo cargado
Fase 7  Q&A          alimenta al asistente; puede EXTRAER de un PDF
Fase 8  Recursos     solicita las imágenes que falten
Fase 9  Generar      build + preview
```

**`get_state` es la brújula.** Devuelve los tres documentos y una lista `missing`
con lo que falta. El agente la consulta al empezar y tras cada cambio, y de ahí
sale la próxima pregunta.

Tres reglas que la práctica hizo explícitas:

- **`missing` es la única autoridad.** Las fases son un orden sugerido, no un
  candado: si el usuario quiere adelantarse, se lo permite.
- **Pedir archivos, no esperarlos.** *«¿Tenés una foto del Cerro Rico? Mandámela
  y la asocio.»*
- **Confirmar no es registrar.** Aceptar una propuesta del agente obliga a llamar
  la tool en ese turno; decir «ya lo guardé» sin haberlo hecho está prohibido.

## Destino o emprendimiento

La fase 0 existe porque el contrato cubre dos casos sin ramificar el código. Lo
que es un «lugar» cambia:

| | Destino | Emprendimiento |
|---|---|---|
| `name` | «Turismo Potosí» | «Hostal Kori Wasi» |
| `places` | atractivos públicos | habitaciones, tours, platos |
| `categories` | `historico`, `naturaleza` | `habitaciones`, `tours` |
| contacto | institucional | **WhatsApp**, por donde llegan las reservas |

El agente deduce cuál es de cómo se presenta el usuario y adapta el vocabulario:
a un emprendedor no le habla de «atractivos del destino» sino de «lo que ofrecés».

## Un turno, por dentro

```
Usuario: "Quiero mostrar el Cerro Rico y la Casa de la Moneda"
   ├─ get_state → falta sitio y lugares
   ├─ el modelo → add_place(Cerro Rico), add_place(Casa de la Moneda)
   ├─ tools.py valida (build_place) y persiste (save_contract)
   └─ "Agregué los dos. ¿Tenés fotos? ¿El Cerro Rico tiene horario?"

Usuario: [adjunta cerro-rico.jpg] "Sí, de 9 a 17"
   ├─ ingest.py: imagen → attach_asset(place=cerro-rico)
   ├─ visión → propone una descripción, espera confirmación
   ├─ el modelo → edit_item(cerro-rico, hours="9-17")
   └─ "Foto asociada y horario cargado. ¿Seguimos con eventos?"
```

En el wizard, cada escritura refresca la vista previa: el sitio toma forma
mientras se conversa. Con un cliente MCP pasa lo mismo si el wizard está abierto
—sondea `GET /api/version` y se actualiza solo—, aunque quien escriba sea otro.

## Archivos

**Imágenes.** Se guardan como asset en el mismo turno en que llegan (enviarla ya
fue la decisión del usuario) y se asocian al lugar o evento. Con un modelo con
visión, además se describen para **proponer** la `description`; ese texto no se
escribe hasta que el usuario lo confirme.

**PDFs.** Se extrae el texto en memoria y entra como contexto para poblar
descripciones y Q&A. **El PDF no se publica**: se destila en contenido del
contrato.

Ninguno de los dos escribe nada por su cuenta: `ingest.py` valida y prepara, y la
persistencia siempre pasa por las intake tools.

## Errores

`run_intake_tool` no lanza: traduce toda excepción a una respuesta accionable y
redactada. El modelo recibe algo que puede relatarle al usuario en vez de una
traza:

> «No se encontró ningún lugar ni evento con id 'x'. Verificá el id e intentá de nuevo.»

## Archivos del módulo

```
puriq/intake/
  tools.py     las 14 intake tools + INTAKE_GUION (fuente única)
  agent.py     el loop conversacional del wizard
  prompt.py    system prompt: embebe el guion, no lo reescribe
  ingest.py    router de imágenes y PDFs
  session.py   historial, para no empezar la charla de cero
```

## Pendiente

- **Endpoint del asistente desplegado.** El chat del sitio publicado redacta con
  LLM sólo si `apiEndpoint` apunta a un servicio corriendo; hoy eso es local.
- **Render i18n.** Las traducciones se generan y guardan, pero la plantilla
  todavía no las muestra.
