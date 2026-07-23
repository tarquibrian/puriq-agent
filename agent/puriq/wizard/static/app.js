/*
 * Puriq Wizard - UI por pasos (vanilla JS, sin toolchain de front-end).
 *
 * Es la capa cliente del wizard: renderiza el flujo por pasos (modulos ->
 * intake de sitio/lugares/eventos -> assets -> Q&A -> marca -> generar ->
 * previsualizar -> publicar), mantiene el estado de la sesion en memoria para
 * no perder datos al navegar adelante/atras (Req 1.2), persiste cada paso via
 * fetch a los endpoints REST (Req 1.3), prellena los campos desde
 * GET /api/state al iniciar (Req 1.5), abre un WebSocket a /ws/build para el
 * progreso en vivo del build (Req 8.2, 8.3), restringe el destino de deploy al
 * catalogo soportado (Req 10.2) y ofrece el enlace de preview cuando esta
 * disponible (Req 9.3). Los errores del servidor ({causa,accion} o
 * {documento,campo,sugerencia}) se muestran como causa + correccion sugerida en
 * el paso donde ocurrieron (Req 7.3).
 */
(function () {
  "use strict";

  // --- Catalogos fijos (espejo del backend) --------------------------------
  // Catalogo de modulos soportado (Req 2.3).
  var MODULE_CATALOG = ["map", "places", "events", "blog", "chatweb"];
  var MODULE_LABELS = {
    map: "Mapa",
    places: "Lugares",
    events: "Eventos",
    blog: "Blog",
    chatweb: "Chat web"
  };
  // Catalogo de secciones de portada soportado (espejo de wizard/landing.py,
  // Req 14.1). El orden aqui es solo el orden por defecto de la lista; el
  // `order` efectivo lo asigna el servidor a partir del orden de esta UI.
  var LANDING_CATALOG = ["hero", "features", "cta", "gallery", "stats", "testimonials", "faq"];
  var LANDING_LABELS = {
    hero: "Hero (portada principal)",
    features: "Destacados",
    cta: "Llamado a la accion",
    gallery: "Galeria",
    stats: "Estadisticas",
    testimonials: "Testimonios",
    faq: "Preguntas frecuentes"
  };
  // Destinos de publicacion soportados (Req 10.2).
  var DEPLOY_TARGETS = [
    "aws-amplify",
    "s3-cloudfront",
    "static-export",
    "vercel",
    "netlify"
  ];

  // --- Estado de la sesion (en memoria del navegador) ----------------------
  // `server` guarda la ultima copia de los 3 contratos (GET /api/state y las
  // respuestas de los endpoints que devuelven el documento fusionado). `draft`
  // guarda lo que el usuario tipea por paso, para que ir y volver no pierda
  // datos aun sin guardar (Req 1.2).
  var state = {
    server: { "tourism-data": {}, "site-config": {}, "theme-tokens": {} },
    draft: {
      modules: [],
      site: {},
      place: {},
      event: {},
      qa: {},
      brand: {},
      landing: [],
      build: { use_llm: true, enrich: false },
      deploy: { target: DEPLOY_TARGETS[0] }
    },
    built: false, // true cuando /ws/build informa "done"
    current: 0
  };

  // --- Utilidades DOM ------------------------------------------------------
  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === "class") node.className = attrs[k];
        else if (k === "html") node.innerHTML = attrs[k];
        else if (k === "text") node.textContent = attrs[k];
        else if (k.indexOf("on") === 0 && typeof attrs[k] === "function") {
          node.addEventListener(k.slice(2).toLowerCase(), attrs[k]);
        } else if (attrs[k] === true) node.setAttribute(k, "");
        else if (attrs[k] !== false && attrs[k] != null) node.setAttribute(k, attrs[k]);
      });
    }
    (children || []).forEach(function (c) {
      if (c == null) return;
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return node;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  var toastTimer = null;
  function toast(message, kind) {
    var t = document.getElementById("toast");
    t.textContent = message;
    t.className = "toast" + (kind ? " " + kind : "");
    t.hidden = false;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      t.hidden = true;
    }, 3500);
  }

  // --- Capa de red + normalizacion de errores ------------------------------
  // Normaliza el cuerpo de error del servidor a {cause, fix, doc}. El backend
  // devuelve {causa, accion} para errores generales o
  // {documento, campo, sugerencia} para validacion de esquema (Req 7.2).
  function normalizeError(body, httpStatus) {
    if (body && typeof body === "object") {
      if (body.causa || body.accion) {
        return { cause: body.causa || "Ocurrio un error.", fix: body.accion || "", doc: "" };
      }
      if (body.documento || body.campo || body.sugerencia) {
        var doc = [];
        if (body.documento) doc.push("Documento: " + body.documento);
        if (body.campo) doc.push("Campo: " + body.campo);
        return {
          cause: body.sugerencia || "El dato no es valido.",
          fix: "",
          doc: doc.join("  \u00b7  ")
        };
      }
      if (body.message) return { cause: body.message, fix: "", doc: "" };
    }
    return {
      cause: "Error del servidor (" + (httpStatus || "?") + ").",
      fix: "Revisa los datos e intenta nuevamente.",
      doc: ""
    };
  }

  // Envuelve fetch: parsea JSON, y ante status >= 400 lanza un objeto de error
  // ya normalizado para que el paso lo muestre (Req 7.3).
  function apiRequest(method, url, opts) {
    opts = opts || {};
    var init = { method: method, headers: {} };
    if (opts.json !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.json);
    } else if (opts.form) {
      init.body = opts.form; // FormData: el navegador fija el Content-Type
    }
    return fetch(url, init).then(function (resp) {
      var ct = resp.headers.get("content-type") || "";
      var parse = ct.indexOf("application/json") !== -1 ? resp.json() : resp.text();
      return parse.then(function (body) {
        if (!resp.ok) {
          throw { __wizardError: true, normalized: normalizeError(body, resp.status), status: resp.status };
        }
        return body;
      });
    });
  }

  // --- Render de mensajes por paso -----------------------------------------
  function renderError(container, normalized) {
    var box = el("div", { class: "step-error", role: "alert" }, [
      el("p", { class: "cause", text: normalized.cause })
    ]);
    if (normalized.fix) box.appendChild(el("p", { class: "fix", text: normalized.fix }));
    if (normalized.doc) box.appendChild(el("p", { class: "doc", text: normalized.doc }));
    container.insertBefore(box, container.firstChild);
  }

  function renderOk(container, message) {
    container.insertBefore(el("div", { class: "step-ok", text: message }), container.firstChild);
  }

  // Muestra el error de una promesa rechazada en el paso actual.
  function handleStepError(container, err) {
    var normalized = err && err.__wizardError ? err.normalized : {
      cause: "No se pudo contactar al servidor.",
      fix: "Verifica que el asistente siga corriendo y reintenta.",
      doc: ""
    };
    renderError(container, normalized);
  }

  // ========================================================================
  // Definicion de pasos
  // ========================================================================
  var STEPS = [
    { id: "modules", label: "Modulos", render: renderModules },
    { id: "site", label: "Sitio", render: renderSite },
    { id: "places", label: "Lugares", render: renderPlaces },
    { id: "events", label: "Eventos", render: renderEvents },
    { id: "assets", label: "Recursos", render: renderAssets },
    { id: "qa", label: "Q&A", render: renderQA },
    { id: "brand", label: "Marca", render: renderBrand },
    { id: "landing", label: "Portada", render: renderLanding },
    { id: "generate", label: "Generar", render: renderGenerate },
    { id: "preview", label: "Previsualizar", render: renderPreview },
    { id: "publish", label: "Publicar", render: renderPublish }
  ];

  // --- Paso: Modulos (Req 2.1-2.3) -----------------------------------------
  function moduleDraftFromServer() {
    var cfg = (state.server["site-config"] || {}).modules || {};
    var rows = MODULE_CATALOG.map(function (key) {
      var existing = cfg[key] || {};
      return {
        key: key,
        enabled: existing.enabled != null ? !!existing.enabled : false,
        order: typeof existing.order === "number" ? existing.order : 999,
        persona: existing.persona || "",
        knowledgeSource: existing.knowledgeSource || ""
      };
    });
    // Ordenar por el `order` existente; el orden de la lista define el order final.
    rows.sort(function (a, b) { return a.order - b.order; });
    return rows;
  }

  function renderModules(container) {
    if (!state.draft.modules.length) state.draft.modules = moduleDraftFromServer();
    var rows = state.draft.modules;

    container.appendChild(el("h2", { text: "1. Modulos del sitio" }));
    container.appendChild(el("p", { class: "hint", text: "Activa las secciones que tendra tu sitio y ordenalas. El orden de la lista define el orden en el sitio." }));

    var list = el("ul", { class: "module-list" });
    rows.forEach(function (row, idx) {
      var checkbox = el("input", {
        type: "checkbox",
        checked: row.enabled,
        onchange: function (e) { row.enabled = e.target.checked; }
      });
      var up = el("button", {
        class: "btn btn-ghost btn-sm", text: "\u2191", title: "Subir",
        onclick: function () { swap(rows, idx, idx - 1); render(); }
      });
      var down = el("button", {
        class: "btn btn-ghost btn-sm", text: "\u2193", title: "Bajar",
        onclick: function () { swap(rows, idx, idx + 1); render(); }
      });
      var li = el("li", null, [
        checkbox,
        el("span", { class: "mod-name", text: MODULE_LABELS[row.key] + " (" + row.key + ")" }),
        el("span", { class: "mod-order", text: "orden " + (idx + 1) }),
        up, down
      ]);
      list.appendChild(li);
    });
    container.appendChild(list);

    // Campos extra para chatweb (persona / knowledgeSource).
    var chat = rows.filter(function (r) { return r.key === "chatweb"; })[0];
    if (chat && chat.enabled) {
      container.appendChild(el("div", { class: "field" }, [
        el("label", { text: "Persona del chat (opcional)" }),
        el("input", {
          type: "text", value: chat.persona,
          oninput: function (e) { chat.persona = e.target.value; }
        })
      ]));
    }

    container.appendChild(el("button", {
      class: "btn", text: "Guardar modulos",
      onclick: function () { saveModules(container); }
    }));
  }

  function swap(arr, i, j) {
    if (j < 0 || j >= arr.length) return;
    var tmp = arr[i]; arr[i] = arr[j]; arr[j] = tmp;
  }

  // Construye la seleccion **ordenada** de modulos para PUT /api/site-config.
  // Se comparte entre el paso Modulos y el paso Portada: como el endpoint exige
  // `modules`, el paso Portada debe reenviar la seleccion actual para no
  // perderla al guardar `landing`. Si el usuario aun no toco el paso Modulos, se
  // deriva la seleccion desde el estado del servidor.
  function currentModulesPayload() {
    var rows = state.draft.modules.length ? state.draft.modules : moduleDraftFromServer();
    return rows.map(function (r) {
      var m = { key: r.key, enabled: r.enabled };
      if (r.key === "chatweb") {
        if (r.persona) m.persona = r.persona;
        if (r.knowledgeSource) m.knowledgeSource = r.knowledgeSource;
      }
      return m;
    });
  }

  function saveModules(container) {
    var payload = { modules: currentModulesPayload() };
    apiRequest("PUT", "/api/site-config", { json: payload })
      .then(function (doc) {
        state.server["site-config"] = doc;
        markDone("modules");
        render();
        renderOk(document.getElementById("step-panel"), "Modulos guardados.");
      })
      .catch(function (err) { handleStepError(container, err); });
  }

  // --- Paso: Sitio (Req 3.1) -----------------------------------------------
  function renderSite(container) {
    var site = (state.server["tourism-data"] || {}).site || {};
    var d = state.draft.site;
    // Prefill desde el servidor solo la primera vez.
    if (d._init !== true) {
      d.name = site.name || "";
      d.region = site.region || "";
      d.defaultLocale = site.defaultLocale || "es";
      var c = site.center || {};
      d.lat = c.lat != null ? c.lat : "";
      d.lng = c.lng != null ? c.lng : "";
      d.zoom = c.zoom != null ? c.zoom : "";
      d._init = true;
    }

    container.appendChild(el("h2", { text: "2. Datos del sitio" }));
    container.appendChild(el("p", { class: "hint", text: "Nombre, region, idioma por defecto y centro del mapa." }));

    container.appendChild(textField("Nombre del sitio", d, "name"));
    container.appendChild(textField("Region", d, "region"));
    container.appendChild(textField("Idioma por defecto (ej. es)", d, "defaultLocale"));
    container.appendChild(el("div", { class: "row" }, [
      textField("Latitud del centro", d, "lat"),
      textField("Longitud del centro", d, "lng")
    ]));
    container.appendChild(textField("Zoom del centro (opcional)", d, "zoom"));

    container.appendChild(el("button", {
      class: "btn", text: "Guardar sitio",
      onclick: function () { saveSite(container); }
    }));
  }

  function saveSite(container) {
    var d = state.draft.site;
    var center = { lat: toNum(d.lat), lng: toNum(d.lng) };
    if (d.zoom !== "" && d.zoom != null) center.zoom = toNum(d.zoom);
    var payload = {
      name: d.name, region: d.region,
      defaultLocale: d.defaultLocale || "es", center: center
    };
    apiRequest("PUT", "/api/tourism-data/site", { json: payload })
      .then(function (doc) {
        state.server["tourism-data"] = doc;
        markDone("site");
        renderOk(document.getElementById("step-panel"), "Sitio guardado.");
      })
      .catch(function (err) { handleStepError(container, err); });
  }

  // --- Paso: Lugares (Req 3.2, 3.4-3.6) ------------------------------------
  function renderPlaces(container) {
    var d = state.draft.place;
    container.appendChild(el("h2", { text: "3. Lugares" }));
    container.appendChild(el("p", { class: "hint", text: "Agrega lugares turisticos. Podes dar coordenadas o solo una direccion (se geocodifica al generar)." }));

    container.appendChild(textField("Nombre", d, "name"));
    container.appendChild(textField("Categoria", d, "category"));
    container.appendChild(el("div", { class: "row" }, [
      textField("Latitud (opcional)", d, "lat"),
      textField("Longitud (opcional)", d, "lng")
    ]));
    container.appendChild(textField("Direccion (opcional)", d, "address"));

    container.appendChild(el("button", {
      class: "btn", text: "Agregar lugar",
      onclick: function () { savePlace(container); }
    }));

    appendSavedList(container, "Lugares guardados", (state.server["tourism-data"] || {}).places, function (p) {
      return p.name + (p.coords ? "" : (p.address ? " (direccion, sin coords)" : ""));
    });
  }

  function savePlace(container) {
    var d = state.draft.place;
    var payload = { name: d.name, category: d.category };
    if (d.lat !== "" && d.lat != null) payload.lat = toNum(d.lat);
    if (d.lng !== "" && d.lng != null) payload.lng = toNum(d.lng);
    if (d.address) payload.address = d.address;
    apiRequest("POST", "/api/tourism-data/places", { json: payload })
      .then(function (doc) {
        state.server["tourism-data"] = doc;
        state.draft.place = {};
        markDone("places");
        render();
        renderOk(document.getElementById("step-panel"), "Lugar agregado.");
      })
      .catch(function (err) { handleStepError(container, err); });
  }

  // --- Paso: Eventos (Req 3.3) ---------------------------------------------
  function renderEvents(container) {
    var d = state.draft.event;
    container.appendChild(el("h2", { text: "4. Eventos" }));
    container.appendChild(el("p", { class: "hint", text: "Agrega festividades y eventos con su fecha de inicio." }));

    container.appendChild(textField("Nombre", d, "name"));
    container.appendChild(el("div", { class: "row" }, [
      textField("Fecha de inicio (AAAA-MM-DD)", d, "startDate"),
      textField("Fecha de fin (opcional)", d, "endDate")
    ]));
    container.appendChild(textField("Lugar asociado (id, opcional)", d, "placeId"));
    container.appendChild(textareaField("Descripcion (opcional)", d, "description"));

    container.appendChild(el("button", {
      class: "btn", text: "Agregar evento",
      onclick: function () { saveEvent(container); }
    }));

    appendSavedList(container, "Eventos guardados", (state.server["tourism-data"] || {}).events, function (ev) {
      return ev.name + (ev.startDate ? " - " + ev.startDate : "");
    });
  }

  function saveEvent(container) {
    var d = state.draft.event;
    var payload = { name: d.name, startDate: d.startDate };
    if (d.endDate) payload.endDate = d.endDate;
    if (d.placeId) payload.placeId = d.placeId;
    if (d.description) payload.description = d.description;
    apiRequest("POST", "/api/tourism-data/events", { json: payload })
      .then(function (doc) {
        state.server["tourism-data"] = doc;
        state.draft.event = {};
        markDone("events");
        render();
        renderOk(document.getElementById("step-panel"), "Evento agregado.");
      })
      .catch(function (err) { handleStepError(container, err); });
  }

  // --- Paso: Recursos / Assets (Req 4.1-4.5) -------------------------------
  function renderAssets(container) {
    container.appendChild(el("h2", { text: "5. Recursos (imagenes y logo)" }));
    container.appendChild(el("p", { class: "hint", text: "Subi fotos de lugares/eventos o el logo de la provincia. Formatos: jpg, png, webp, gif, svg, avif." }));

    var fileInput = el("input", { type: "file", accept: "image/*" });
    var targetSel = el("select", null, [
      el("option", { value: "", text: "Sin asociar (solo subir)" }),
      el("option", { value: "logo", text: "Logo de marca" }),
      el("option", { value: "place", text: "Imagen de un lugar" }),
      el("option", { value: "event", text: "Imagen de un evento" })
    ]);
    var idInput = el("input", { type: "text", placeholder: "id del lugar/evento (si aplica)" });

    container.appendChild(el("div", { class: "field" }, [el("label", { text: "Archivo" }), fileInput]));
    container.appendChild(el("div", { class: "field" }, [el("label", { text: "Asociar a" }), targetSel]));
    container.appendChild(el("div", { class: "field" }, [el("label", { text: "Id destino (para lugar/evento)" }), idInput]));

    container.appendChild(el("button", {
      class: "btn", text: "Subir recurso",
      onclick: function () {
        if (!fileInput.files || !fileInput.files[0]) {
          renderError(container, { cause: "Selecciona un archivo primero.", fix: "", doc: "" });
          return;
        }
        var fd = new FormData();
        fd.append("file", fileInput.files[0]);
        if (targetSel.value) fd.append("target", targetSel.value);
        if (idInput.value) fd.append("id", idInput.value);
        apiRequest("POST", "/api/assets", { form: fd })
          .then(function (res) {
            if (res.document) {
              // El asset se enlazo al contrato (tourism-data o theme-tokens).
              if (res.document.places || res.document.events) state.server["tourism-data"] = res.document;
              else state.server["theme-tokens"] = res.document;
            }
            markDone("assets");
            renderOk(document.getElementById("step-panel"), "Recurso guardado en: " + res.path);
          })
          .catch(function (err) { handleStepError(container, err); });
      }
    }));
  }

  // --- Paso: Q&A (Req 5.1, 5.4) --------------------------------------------
  function renderQA(container) {
    var d = state.draft.qa;
    container.appendChild(el("h2", { text: "6. Preguntas y respuestas" }));
    container.appendChild(el("p", { class: "hint", text: "Conocimiento para el futuro chat del sitio. Pregunta y respuesta no pueden quedar vacias." }));

    container.appendChild(textField("Pregunta", d, "question"));
    container.appendChild(textareaField("Respuesta", d, "answer"));

    container.appendChild(el("button", {
      class: "btn", text: "Agregar Q&A",
      onclick: function () {
        apiRequest("POST", "/api/qa", { json: { question: d.question || "", answer: d.answer || "" } })
          .then(function (res) {
            if (res.document) state.server["site-config"] = res.document;
            state.draft.qa = {};
            markDone("qa");
            render();
            renderOk(document.getElementById("step-panel"), "Q&A guardada (fuente: " + res.knowledgeSource + ").");
          })
          .catch(function (err) { handleStepError(container, err); });
      }
    }));
  }

  // --- Paso: Marca (Req 6.1-6.4) -------------------------------------------
  function renderBrand(container) {
    var theme = state.server["theme-tokens"] || {};
    var d = state.draft.brand;
    if (d._init !== true) {
      var colors = theme.colors || {};
      var typo = theme.typography || {};
      d.primary = colors.primary || "";
      d.secondary = colors.secondary || "";
      d.background = colors.background || "";
      d.text = colors.text || "";
      d.accent = colors.accent || "";
      d.headingFont = typo.headingFont || "";
      d.bodyFont = typo.bodyFont || "";
      d.tone = (theme.voice || {}).tone || "";
      d._init = true;
    }

    container.appendChild(el("h2", { text: "7. Marca" }));
    container.appendChild(el("p", { class: "hint", text: "Colores en formato hexadecimal (ej. #C1440E), tipografia y tono de voz." }));

    container.appendChild(el("div", { class: "row" }, [
      textField("Color primario (#hex)", d, "primary"),
      textField("Color secundario (#hex, opcional)", d, "secondary")
    ]));
    container.appendChild(el("div", { class: "row" }, [
      textField("Color de fondo (#hex)", d, "background"),
      textField("Color de texto (#hex)", d, "text")
    ]));
    container.appendChild(textField("Color de acento (#hex, opcional)", d, "accent"));
    container.appendChild(el("div", { class: "row" }, [
      textField("Tipografia de titulos", d, "headingFont"),
      textField("Tipografia de cuerpo", d, "bodyFont")
    ]));
    container.appendChild(textField("Tono de voz (opcional)", d, "tone"));

    container.appendChild(el("button", {
      class: "btn", text: "Guardar marca",
      onclick: function () { saveBrand(container); }
    }));
  }

  function saveBrand(container) {
    var d = state.draft.brand;
    var payload = {};
    var colors = {};
    ["primary", "secondary", "background", "text", "accent"].forEach(function (k) {
      if (d[k]) colors[k] = d[k];
    });
    if (Object.keys(colors).length) payload.colors = colors;
    var typo = {};
    if (d.headingFont) typo.headingFont = d.headingFont;
    if (d.bodyFont) typo.bodyFont = d.bodyFont;
    if (Object.keys(typo).length) payload.typography = typo;
    if (d.tone) payload.tone = d.tone;

    apiRequest("PUT", "/api/theme-tokens", { json: payload })
      .then(function (doc) {
        state.server["theme-tokens"] = doc;
        markDone("brand");
        renderOk(document.getElementById("step-panel"), "Marca guardada.");
      })
      .catch(function (err) { handleStepError(container, err); });
  }

  // --- Paso: Portada (Req 14.1-14.3, 14.5, 14.6) ---------------------------
  //
  // Lista las Landing_Section del catalogo soportado con controles de
  // activar/desactivar y reordenar (el orden de la lista define el orden que se
  // envia; el servidor asigna el `order`, Req 14.2), y permite editar el copy de
  // cada seccion (Req 14.3). Persiste via PUT /api/site-config enviando `landing`
  // como lista ordenada de {type, enabled, content} JUNTO con la seleccion de
  // modulos actual (el endpoint exige `modules`, por eso se reenvia para no
  // perderla). Prellena desde GET /api/state `site-config.landing` (Req 14.5). El
  // Wizard solo compone secciones pre-construidas; nunca genera codigo (Req 14.6).

  // Normaliza el `content` del servidor a la forma editable del draft segun el
  // tipo de seccion, conservando el resto de campos en `_raw` para no perderlos
  // al guardar (p. ej. background/overlay/cta del hero configurados aparte).
  function makeLandingRow(type, enabled, content) {
    content = content || {};
    var c = {};
    if (type === "hero") {
      c.headline = content.headline || "";
      c.subheadline = content.subheadline || "";
    } else if (type === "features") {
      c.title = content.title || "";
      c.items = (content.items || []).map(function (it) {
        return { title: (it && it.title) || "", description: (it && it.description) || "" };
      });
    } else if (type === "cta") {
      c.message = content.message || "";
      var cta = content.cta || {};
      c.ctaLabel = cta.label || "";
      c.ctaHref = cta.href || "";
    } else if (type === "stats") {
      c.metrics = (content.metrics || []).map(function (m) {
        return { value: (m && m.value) || "", label: (m && m.label) || "" };
      });
    } else if (type === "gallery") {
      c.images = (content.images || []).map(function (im) {
        return { src: (im && im.src) || "", alt: (im && im.alt) || "" };
      });
    } else if (type === "testimonials") {
      c.eyebrow = content.eyebrow || "";
      c.title = content.title || "";
      c.items = (content.items || []).map(function (it) {
        return {
          quote: (it && it.quote) || "",
          author: (it && it.author) || "",
          role: (it && it.role) || ""
        };
      });
    } else if (type === "faq") {
      c.eyebrow = content.eyebrow || "";
      c.title = content.title || "";
      c.items = (content.items || []).map(function (it) {
        return { question: (it && it.question) || "", answer: (it && it.answer) || "" };
      });
    }
    return { type: type, enabled: enabled, content: c, _raw: content };
  }

  // Construye el draft del paso a partir de `site-config.landing` del servidor
  // (Req 14.5). Respeta el orden guardado y completa el catalogo con las
  // secciones ausentes (desactivadas por defecto) para que el usuario pueda
  // activarlas.
  function landingDraftFromServer() {
    var existing = (state.server["site-config"] || {}).landing || [];
    var sorted = existing
      .filter(function (s) { return s && LANDING_CATALOG.indexOf(s.type) !== -1; })
      .slice()
      .sort(function (a, b) { return (a.order || 0) - (b.order || 0); });

    var rows = [];
    var seen = {};
    sorted.forEach(function (s) {
      if (seen[s.type]) return; // un tipo por seccion en la UI
      seen[s.type] = true;
      rows.push(makeLandingRow(s.type, !!s.enabled, s.content || {}));
    });
    LANDING_CATALOG.forEach(function (type) {
      if (!seen[type]) rows.push(makeLandingRow(type, false, {}));
    });
    return rows;
  }

  // Convierte el draft editable de una seccion al `content` del contrato,
  // fusionando sobre `_raw` para preservar campos no editados en esta UI.
  function landingContentToPayload(row) {
    var raw = row._raw || {};
    var c = row.content;
    var out = {};
    Object.keys(raw).forEach(function (k) { out[k] = raw[k]; });
    if (row.type === "hero") {
      out.headline = c.headline;
      out.subheadline = c.subheadline;
    } else if (row.type === "features") {
      out.title = c.title;
      out.items = c.items.map(function (it) {
        return { title: it.title, description: it.description };
      });
    } else if (row.type === "cta") {
      out.message = c.message;
      var cta = {};
      var rawCta = raw.cta || {};
      Object.keys(rawCta).forEach(function (k) { cta[k] = rawCta[k]; });
      cta.label = c.ctaLabel;
      cta.href = c.ctaHref;
      out.cta = cta;
    } else if (row.type === "stats") {
      out.metrics = c.metrics.map(function (m) {
        return { value: m.value, label: m.label };
      });
    } else if (row.type === "gallery") {
      out.images = c.images.map(function (im) {
        return { src: im.src, alt: im.alt };
      });
    } else if (row.type === "testimonials") {
      out.eyebrow = c.eyebrow;
      out.title = c.title;
      out.items = c.items.map(function (it) {
        return { quote: it.quote, author: it.author, role: it.role };
      });
    } else if (row.type === "faq") {
      out.eyebrow = c.eyebrow;
      out.title = c.title;
      out.items = c.items.map(function (it) {
        return { question: it.question, answer: it.answer };
      });
    }
    return out;
  }

  function renderLanding(container) {
    if (!state.draft.landing.length) state.draft.landing = landingDraftFromServer();
    var rows = state.draft.landing;

    container.appendChild(el("h2", { text: "8. Portada" }));
    container.appendChild(el("p", { class: "hint", text: "Activa, ordena y edita las secciones de la portada. El orden de la lista define el orden en el sitio; el asistente solo compone secciones ya construidas." }));

    var list = el("div", { class: "landing-list" });
    rows.forEach(function (row, idx) {
      list.appendChild(renderLandingSection(row, idx, rows));
    });
    container.appendChild(list);

    container.appendChild(el("button", {
      class: "btn", text: "Guardar portada",
      onclick: function () { saveLanding(container); }
    }));
  }

  function renderLandingSection(row, idx, rows) {
    var checkbox = el("input", {
      type: "checkbox", checked: row.enabled,
      onchange: function (e) { row.enabled = e.target.checked; }
    });
    var up = el("button", {
      class: "btn btn-ghost btn-sm", text: "\u2191", title: "Subir",
      onclick: function () { swap(rows, idx, idx - 1); render(); }
    });
    var down = el("button", {
      class: "btn btn-ghost btn-sm", text: "\u2193", title: "Bajar",
      onclick: function () { swap(rows, idx, idx + 1); render(); }
    });

    var head = el("div", { class: "landing-head" }, [
      el("label", { class: "landing-toggle" }, [
        checkbox,
        el("span", { class: "mod-name", text: LANDING_LABELS[row.type] + " (" + row.type + ")" })
      ]),
      el("span", { class: "mod-order", text: "orden " + (idx + 1) }),
      up, down
    ]);

    var body = el("div", { class: "landing-body" });
    renderLandingFields(body, row);

    return el("div", { class: "landing-section" }, [head, body]);
  }

  // Renderiza los campos de copy editables segun el tipo de seccion (Req 14.3).
  function renderLandingFields(body, row) {
    var c = row.content;
    if (row.type === "hero") {
      body.appendChild(textField("Titular", c, "headline"));
      body.appendChild(textField("Subtitulo", c, "subheadline"));
    } else if (row.type === "features") {
      body.appendChild(textField("Titulo de la seccion (opcional)", c, "title"));
      renderItemList(body, "Destacados", c.items,
        function () { return { title: "", description: "" }; },
        function (item, itemBody) {
          itemBody.appendChild(textField("Titulo", item, "title"));
          itemBody.appendChild(textField("Descripcion", item, "description"));
        });
    } else if (row.type === "cta") {
      body.appendChild(textField("Mensaje", c, "message"));
      body.appendChild(el("div", { class: "row" }, [
        textField("Etiqueta del boton", c, "ctaLabel"),
        textField("Destino (href)", c, "ctaHref")
      ]));
    } else if (row.type === "stats") {
      renderItemList(body, "Metricas", c.metrics,
        function () { return { value: "", label: "" }; },
        function (item, itemBody) {
          itemBody.appendChild(el("div", { class: "row" }, [
            textField("Valor", item, "value"),
            textField("Etiqueta", item, "label")
          ]));
        });
    } else if (row.type === "gallery") {
      renderItemList(body, "Imagenes", c.images,
        function () { return { src: "", alt: "" }; },
        function (item, itemBody) {
          itemBody.appendChild(el("div", { class: "row" }, [
            textField("Ruta de imagen (src)", item, "src"),
            textField("Texto alternativo (alt)", item, "alt")
          ]));
        });
    } else if (row.type === "testimonials") {
      body.appendChild(textField("Antetitulo (opcional)", c, "eyebrow"));
      body.appendChild(textField("Titulo de la seccion (opcional)", c, "title"));
      renderItemList(body, "Testimonios", c.items,
        function () { return { quote: "", author: "", role: "" }; },
        function (item, itemBody) {
          itemBody.appendChild(textareaField("Testimonio (quote)", item, "quote"));
          itemBody.appendChild(el("div", { class: "row" }, [
            textField("Autor", item, "author"),
            textField("Rol (opcional)", item, "role")
          ]));
        });
    } else if (row.type === "faq") {
      body.appendChild(textField("Antetitulo (opcional)", c, "eyebrow"));
      body.appendChild(textField("Titulo de la seccion (opcional)", c, "title"));
      renderItemList(body, "Preguntas frecuentes", c.items,
        function () { return { question: "", answer: "" }; },
        function (item, itemBody) {
          itemBody.appendChild(textField("Pregunta", item, "question"));
          itemBody.appendChild(textareaField("Respuesta", item, "answer"));
        });
    }
  }

  // Editor de listas de sub-items (destacados, metricas, imagenes) con agregar y
  // quitar. Muta el array del draft y re-renderiza para reflejar el cambio.
  function renderItemList(body, title, arr, makeEmpty, renderItemFields) {
    body.appendChild(el("h4", { class: "landing-subtitle", text: title }));
    arr.forEach(function (item, i) {
      var itemBody = el("div", { class: "landing-subitem" });
      renderItemFields(item, itemBody);
      itemBody.appendChild(el("button", {
        class: "btn btn-ghost btn-sm", text: "Quitar",
        onclick: function () { arr.splice(i, 1); render(); }
      }));
      body.appendChild(itemBody);
    });
    body.appendChild(el("button", {
      class: "btn btn-ghost btn-sm", text: "+ Agregar",
      onclick: function () { arr.push(makeEmpty()); render(); }
    }));
  }

  function saveLanding(container) {
    var rows = state.draft.landing;
    var payload = {
      modules: currentModulesPayload(),
      landing: rows.map(function (row) {
        return { type: row.type, enabled: row.enabled, content: landingContentToPayload(row) };
      })
    };
    apiRequest("PUT", "/api/site-config", { json: payload })
      .then(function (doc) {
        state.server["site-config"] = doc;
        markDone("landing");
        renderOk(document.getElementById("step-panel"), "Portada guardada.");
      })
      .catch(function (err) { handleStepError(container, err); });
  }

  // --- Paso: Generar (WebSocket /ws/build, Req 8.2-8.4) --------------------
  function renderGenerate(container) {
    var d = state.draft.build;
    container.appendChild(el("h2", { text: "9. Generar el sitio" }));
    container.appendChild(el("p", { class: "hint", text: "Dispara la generacion y observa el progreso en vivo." }));

    var llm = el("input", { type: "checkbox", checked: d.use_llm, onchange: function (e) { d.use_llm = e.target.checked; } });
    var enrich = el("input", { type: "checkbox", checked: d.enrich, onchange: function (e) { d.enrich = e.target.checked; } });
    container.appendChild(el("div", { class: "field" }, [
      el("label", null, [llm, document.createTextNode(" Enriquecer contenido con LLM")])
    ]));
    container.appendChild(el("div", { class: "field" }, [
      el("label", null, [enrich, document.createTextNode(" Enriquecer al recolectar")])
    ]));

    var logBox = el("div", { class: "progress-log", id: "build-log", hidden: true });
    var startBtn = el("button", {
      class: "btn", text: "Generar",
      onclick: function () { startBuild(container, startBtn, logBox); }
    });
    container.appendChild(startBtn);
    container.appendChild(logBox);
  }

  function logLine(box, text, kind) {
    box.hidden = false;
    box.appendChild(el("div", { class: "line" + (kind ? " " + kind : ""), text: text }));
    box.scrollTop = box.scrollHeight;
  }

  function startBuild(container, startBtn, logBox) {
    clear(logBox);
    logBox.hidden = false;
    startBtn.disabled = true;

    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    var ws;
    try {
      ws = new WebSocket(proto + "//" + location.host + "/ws/build");
    } catch (e) {
      startBtn.disabled = false;
      renderError(container, { cause: "No se pudo abrir la conexion de build.", fix: "Reintenta.", doc: "" });
      return;
    }

    ws.onopen = function () {
      logLine(logBox, "Conectado. Iniciando generacion...");
      ws.send(JSON.stringify({ use_llm: state.draft.build.use_llm, enrich: state.draft.build.enrich }));
    };
    ws.onmessage = function (evt) {
      var msg;
      try { msg = JSON.parse(evt.data); } catch (e) { logLine(logBox, evt.data); return; }
      if (msg.type === "progress") {
        logLine(logBox, msg.message || "...");
      } else if (msg.type === "done") {
        logLine(logBox, "Listo. Sitio construido en: " + (msg.distPath || "dist/"), "done");
        state.built = true;
        markDone("generate");
        startBtn.disabled = false;
        toast("Sitio generado. Ya podes previsualizar.", "ok");
      } else if (msg.type === "error") {
        logLine(logBox, "Error: " + (msg.message || "fallo la generacion"), "err");
        startBtn.disabled = false;
      }
    };
    ws.onerror = function () {
      logLine(logBox, "Error de conexion durante la generacion.", "err");
      startBtn.disabled = false;
    };
    ws.onclose = function () {
      startBtn.disabled = false;
    };
  }

  // --- Paso: Previsualizar (Req 9.1-9.3) -----------------------------------
  function renderPreview(container) {
    container.appendChild(el("h2", { text: "10. Previsualizar" }));
    container.appendChild(el("p", { class: "hint", text: "Abri el sitio construido en tu navegador antes de publicarlo." }));

    container.appendChild(el("button", {
      class: "btn", text: "Iniciar previsualizacion",
      onclick: function () {
        apiRequest("POST", "/api/preview", { json: {} })
          .then(function (res) {
            var panel = document.getElementById("step-panel");
            if (res.url) {
              markDone("preview");
              var link = el("a", { class: "preview-link", href: res.url, target: "_blank", rel: "noopener", text: "Abrir previsualizacion: " + res.url });
              panel.insertBefore(link, panel.querySelector("button").nextSibling);
            } else if (res.message) {
              // Sin build disponible (Req 9.2).
              renderError(container, { cause: res.message, fix: "Genera el sitio en el paso 8.", doc: "" });
            }
          })
          .catch(function (err) { handleStepError(container, err); });
      }
    }));
  }

  // --- Paso: Publicar (Req 10.1-10.4) --------------------------------------
  function renderPublish(container) {
    var d = state.draft.deploy;
    container.appendChild(el("h2", { text: "11. Publicar" }));
    container.appendChild(el("p", { class: "hint", text: "Elegi el destino y publica tu sitio para obtener una URL." }));

    var sel = el("select", { onchange: function (e) { d.target = e.target.value; } },
      DEPLOY_TARGETS.map(function (t) {
        return el("option", { value: t, text: t, selected: t === d.target });
      })
    );
    container.appendChild(el("div", { class: "field" }, [el("label", { text: "Destino" }), sel]));

    container.appendChild(el("button", {
      class: "btn", text: "Publicar",
      onclick: function () {
        apiRequest("POST", "/api/deploy", { json: { target: d.target } })
          .then(function (res) {
            var panel = document.getElementById("step-panel");
            if (res.url) {
              if (res.document) state.server["site-config"] = res.document;
              markDone("publish");
              var link = el("a", { class: "preview-link", href: res.url, target: "_blank", rel: "noopener", text: "Sitio publicado: " + res.url });
              panel.insertBefore(link, panel.querySelector("button").nextSibling);
            } else if (res.message) {
              // Sin build disponible (Req 10.3).
              renderError(container, { cause: res.message, fix: "Genera el sitio en el paso 8.", doc: "" });
            }
          })
          .catch(function (err) { handleStepError(container, err); });
      }
    }));
  }

  // --- Helpers de formulario -----------------------------------------------
  function textField(labelText, obj, key) {
    var input = el("input", {
      type: "text",
      value: obj[key] != null ? obj[key] : "",
      oninput: function (e) { obj[key] = e.target.value; }
    });
    return el("div", { class: "field" }, [el("label", { text: labelText }), input]);
  }

  function textareaField(labelText, obj, key) {
    var ta = el("textarea", { oninput: function (e) { obj[key] = e.target.value; } });
    ta.value = obj[key] != null ? obj[key] : "";
    return el("div", { class: "field" }, [el("label", { text: labelText }), ta]);
  }

  function appendSavedList(container, title, items, describe) {
    if (!items || !items.length) return;
    container.appendChild(el("h3", { text: title }));
    var ul = el("ul", { class: "saved-list" });
    items.forEach(function (it) { ul.appendChild(el("li", { text: describe(it) })); });
    container.appendChild(ul);
  }

  function toNum(v) {
    var n = Number(v);
    return isNaN(n) ? v : n;
  }

  // --- Estado de "paso completado" para la navegacion lateral --------------
  var doneSteps = {};
  function markDone(id) { doneSteps[id] = true; renderNav(); }

  // ========================================================================
  // Navegacion y montaje
  // ========================================================================
  function renderNav() {
    var nav = document.getElementById("stepnav");
    clear(nav);
    STEPS.forEach(function (step, idx) {
      var cls = "";
      if (idx === state.current) cls += " active";
      if (doneSteps[step.id]) cls += " done";
      var btn = el("button", {
        class: cls.trim(),
        onclick: function () { goTo(idx); }
      }, [
        el("span", { class: "dot", text: doneSteps[step.id] ? "\u2713" : String(idx + 1) }),
        document.createTextNode(step.label)
      ]);
      nav.appendChild(btn);
    });
  }

  function render() {
    var panel = document.getElementById("step-panel");
    clear(panel);
    STEPS[state.current].render(panel);
    document.getElementById("step-indicator").textContent =
      "Paso " + (state.current + 1) + " de " + STEPS.length;
    document.getElementById("btn-prev").disabled = state.current === 0;
    document.getElementById("btn-next").disabled = state.current === STEPS.length - 1;
    renderNav();
  }

  function goTo(idx) {
    if (idx < 0 || idx >= STEPS.length) return;
    state.current = idx;
    render();
  }

  function init() {
    document.getElementById("btn-prev").addEventListener("click", function () { goTo(state.current - 1); });
    document.getElementById("btn-next").addEventListener("click", function () { goTo(state.current + 1); });

    // Prellenar desde el estado del servidor (Req 1.5).
    apiRequest("GET", "/api/state")
      .then(function (data) {
        if (data && typeof data === "object") {
          state.server["tourism-data"] = data["tourism-data"] || {};
          state.server["site-config"] = data["site-config"] || {};
          state.server["theme-tokens"] = data["theme-tokens"] || {};
        }
      })
      .catch(function () {
        toast("No se pudo cargar el estado previo; se arranca en blanco.", "err");
      })
      .then(function () { render(); });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
