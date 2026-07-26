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
  // --- Catalogo tipografico (espejo de template/src/design-system/fonts.ts) --
  // Ese archivo es la unica fuente de verdad de las pilas de respaldo; aca solo
  // se replican los NOMBRES y una pila corta para la vista previa del wizard.
  // Si se agrega una familia alla, agregarla aca para que aparezca en el paso.
  var FONT_CATALOG = [
    { name: "Playfair Display", slug: "playfair-display", stack: '"Iowan Old Style", Palatino, Georgia, serif' },
    { name: "Lora", slug: "lora", stack: '"Iowan Old Style", Constantia, Georgia, serif' },
    { name: "Merriweather", slug: "merriweather", stack: "Georgia, Cambria, serif" },
    { name: "Source Serif 4", slug: "source-serif-4", stack: "Charter, Cambria, Georgia, serif" },
    { name: "Inter", slug: "inter", stack: 'system-ui, "Segoe UI", Roboto, sans-serif' },
    { name: "Work Sans", slug: "work-sans", stack: 'system-ui, "Segoe UI", Roboto, sans-serif' },
    { name: "Source Sans 3", slug: "source-sans-3", stack: 'system-ui, "Segoe UI", Roboto, sans-serif' },
    { name: "Poppins", slug: "poppins", stack: '"Avenir Next", "Century Gothic", system-ui, sans-serif' },
    { name: "Montserrat", slug: "montserrat", stack: '"Avenir Next", "Century Gothic", system-ui, sans-serif' },
    { name: "DM Sans", slug: "dm-sans", stack: '"Avenir Next", system-ui, sans-serif' }
  ];

  // Duplas titulo+cuerpo que combinan bien (contraste de forma sin chocar).
  var FONT_PAIRINGS = [
    { heading: "Playfair Display", body: "Inter" },
    { heading: "Poppins", body: "Inter" },
    { heading: "Lora", body: "Source Sans 3" },
    { heading: "Montserrat", body: "Work Sans" }
  ];

  // Paletas de arranque, construidas sobre principios de UX/UI, no solo "colores
  // lindos". Cada una sigue la regla 60-30-10:
  //   - FONDO NEUTRO (60%): casi blanco, con un tinte MINIMO. La base no compite;
  //     deja respirar al contenido. Un fondo muy tenido se ve recargado.
  //   - PRIMARY (30%): el color de marca, en header, botones y bandas de enfasis.
  //   - ACCENT (10%): el UNICO pop, complementario del primary. Es lo que "da
  //     juego" —eyebrows, enlaces, detalles— usado con moderacion.
  //   - SECONDARY: una variante mas clara del PROPIO primary (misma familia), no
  //     un tercer color distinto. Asi los colores se relacionan y armonizan en
  //     vez de chocar.
  //   - TEXT: casi negro, para lectura holgada sobre el fondo claro.
  //
  // Contrastes verificados: texto/fondo >= 15:1, primary/blanco >= 4.5:1 (texto
  // blanco en botones), accent/fondo >= 4:1 (eyebrows y enlaces legibles).
  var PALETTES = [
    // Teal profundo + coral. Frio sereno con un pop calido.
    { name: "Oceano", colors: { primary: "#0E6E6D", secondary: "#5EAAA8", background: "#F7FAFA", text: "#132322", accent: "#D14A2C" } },
    // Verde bosque + terracota. Natural, ideal para turismo.
    { name: "Bosque", colors: { primary: "#1E6B4F", secondary: "#74A892", background: "#F8FAF7", text: "#15221C", accent: "#C15B2E" } },
    // Indigo + dorado tostado. Confiable y moderno.
    { name: "Indigo", colors: { primary: "#3B3A8F", secondary: "#8C8AC9", background: "#F8F8FC", text: "#16162A", accent: "#B45309" } },
    // Borgona + teal. Patrimonial y elegante, con un juego frio.
    { name: "Borgona", colors: { primary: "#7A2233", secondary: "#B57A84", background: "#FBF8F6", text: "#241318", accent: "#0E7C7B" } },
    // Pizarra + azul brillante. Sobria e institucional, con un acento nitido.
    { name: "Pizarra", colors: { primary: "#334155", secondary: "#8593A8", background: "#F8FAFC", text: "#0F172A", accent: "#2563EB" } },
    // Cobre + teal. Calida y terrosa con un contrapunto frio.
    { name: "Cobre", colors: { primary: "#B04A12", secondary: "#E0A96D", background: "#FBFAF8", text: "#231710", accent: "#0E6E6D" } }
  ];

  /** Pila CSS de una familia para la vista previa (familia + respaldos). */
  function fontStackOf(name) {
    var entry = FONT_CATALOG.filter(function (f) { return f.name === name; })[0];
    if (!entry) return "system-ui, sans-serif";
    return '"' + entry.name + '", ' + entry.stack;
  }

  /** True si la familia viaja con el sitio (hay `.woff2` en el catalogo). */
  function isSelfHosted(name) {
    var entry = FONT_CATALOG.filter(function (f) { return f.name === name; })[0];
    if (!entry || !state.fontFiles) return false;
    return state.fontFiles.some(function (f) { return f.indexOf(entry.slug + "-") === 0; });
  }

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
      asset: { target: "", id: "" },
      brand: {},
      landing: [],
      build: { use_llm: true, enrich: false },
      deploy: { target: DEPLOY_TARGETS[0] }
    },
    // Inventarios que NO viven en los 3 contratos y se piden aparte:
    // `assets` = archivos de /assets (GET /api/assets), `qa` = entradas de
    // content/qa.json (GET /api/qa). `null` significa "todavia no cargado" y
    // dispara la carga perezosa la primera vez que se entra al paso.
    assets: null,
    qa: null,
    // Archivos `.woff2` que trae el catalogo de la Template (GET /api/fonts).
    // Sirven para marcar que familias viajan con el sitio y para inyectar los
    // `@font-face` de la vista previa del paso Marca.
    fontFiles: null,
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
  // Los avisos del paso se REEMPLAZAN, no se apilan: al fallar un guardado
  // quedaba el "guardado con exito" del intento anterior justo encima del error
  // nuevo, y el usuario no sabia cual de los dos describia el estado real.
  function clearMessages(container) {
    var panel = container || document.getElementById("step-panel");
    if (!panel) return;
    panel.querySelectorAll(".step-error, .step-ok").forEach(function (n) {
      n.remove();
    });
  }

  function renderError(container, normalized) {
    clearMessages(container);
    var box = el("div", { class: "step-error", role: "alert" }, [
      el("p", { class: "cause", text: normalized.cause })
    ]);
    if (normalized.fix) box.appendChild(el("p", { class: "fix", text: normalized.fix }));
    if (normalized.doc) box.appendChild(el("p", { class: "doc", text: normalized.doc }));
    container.insertBefore(box, container.firstChild);
  }

  function renderOk(container, message) {
    clearMessages(container);
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
  // Icono de linea por paso (SVG inline, sin dependencias). Da un anclaje visual
  // para reconocer cada paso de un vistazo. `stroke: currentColor` hace que
  // hereden el color segun el estado (tenue/activo/hecho) desde el CSS.
  var STEP_ICONS = {
    modules: '<path d="M4 5h6v6H4zM14 5h6v6h-6zM4 15h6v4H4zM14 15h6v4h-6z"/>',
    site: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.5 2.4 2.5 14.6 0 17M12 3.5c-2.5 2.4-2.5 14.6 0 17"/>',
    places: '<path d="M12 21c4-4.5 6.5-7.6 6.5-11a6.5 6.5 0 1 0-13 0c0 3.4 2.5 6.5 6.5 11Z"/><circle cx="12" cy="10" r="2.3"/>',
    events: '<rect x="3.5" y="5" width="17" height="15" rx="2"/><path d="M3.5 9.5h17M8 3.5v3M16 3.5v3"/>',
    assets: '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><circle cx="9" cy="10" r="1.8"/><path d="m4 17 5-4 5 4 3-3 3 2.5"/>',
    qa: '<path d="M4 5h16v11H8l-4 4V5Z"/><path d="M9 9.5a2 2 0 1 1 3 1.7c-.6.4-1 .8-1 1.6"/><path d="M11 15h.01"/>',
    brand: '<path d="M12 3.5a8.5 8.5 0 1 0 0 17c1.4 0 2-1 2-2s-.8-1.6-.8-2.6.9-1.9 2-1.9h1.3A4.2 4.2 0 0 0 20 9.8C20 6.3 16.4 3.5 12 3.5Z"/><circle cx="8" cy="11" r="1"/><circle cx="12" cy="8" r="1"/><circle cx="16" cy="11" r="1"/>',
    landing: '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M3.5 9h17M8 9v10.5"/>',
    generate: '<path d="M12 3.5 13.7 9l5.5 1.7-5.5 1.7L12 18l-1.7-5.6L4.8 10.7 10.3 9 12 3.5Z"/><path d="M18.5 4v3M20 5.5h-3"/>',
    preview: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z"/><circle cx="12" cy="12" r="2.7"/>',
    publish: '<path d="M12 3.5 18 9h-4v7h-4V9H6l6-5.5Z"/><path d="M6 20h12"/>'
  };

  // Envuelve el path del icono en un <svg> de linea uniforme.
  function stepIconSvg(id) {
    var inner = STEP_ICONS[id] || "";
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" ' +
      'aria-hidden="true">' + inner + "</svg>";
  }

  // Cada paso lleva, ademas del `label` de la navegacion, un `title` y una
  // `desc` que la barra lateral muestra como cabecera del paso actual (el patron
  // de la referencia: titulo grande + descripcion a la izquierda). El `render`
  // del panel sigue aportando el subtitulo del contenido.
  var STEPS = [
    { id: "modules", label: "Modulos", title: "Modulos", desc: "Elegi que secciones tendra tu sitio y en que orden.", render: renderModules },
    { id: "site", label: "Sitio", title: "Datos del sitio", desc: "Nombre, region, direccion web y contacto del destino.", render: renderSite },
    { id: "places", label: "Lugares", title: "Lugares", desc: "Carga los lugares turisticos que queres mostrar.", render: renderPlaces },
    { id: "events", label: "Eventos", title: "Eventos", desc: "Festividades y fechas clave del destino.", render: renderEvents },
    { id: "assets", label: "Recursos", title: "Recursos", desc: "Subi las fotos y el logo del destino.", render: renderAssets },
    { id: "qa", label: "Q&A", title: "Preguntas y respuestas", desc: "Alimenta al asistente con respuestas oficiales.", render: renderQA },
    { id: "brand", label: "Marca", title: "Marca", desc: "Colores y tipografias que definen la identidad visual.", render: renderBrand },
    { id: "landing", label: "Portada", title: "Portada", desc: "Arma y ordena las secciones de la pagina principal.", render: renderLanding },
    { id: "generate", label: "Generar", title: "Generar el sitio", desc: "Construi el sitio a partir de todo lo cargado.", render: renderGenerate },
    { id: "preview", label: "Previsualizar", title: "Previsualizar", desc: "Revisa el sitio construido antes de publicarlo.", render: renderPreview },
    { id: "publish", label: "Publicar", title: "Publicar", desc: "Deja el sitio en linea en el destino elegido.", render: renderPublish }
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

    container.appendChild(el("h2", { text: "Modulos del sitio" }));
    container.appendChild(el("p", { class: "hint", text: "Activa las secciones que tendra tu sitio y ordenalas. El orden de la lista define el orden en el sitio." }));

    var list = el("ul", { class: "module-list" });
    rows.forEach(function (row, idx) {
      var checkbox = el("input", {
        type: "checkbox",
        checked: row.enabled,
        // Activar/desactivar un modulo se refleja al instante en el
        // previsualizador, sin esperar a "Guardar" (lee el borrador en vivo).
        onchange: function (e) { row.enabled = e.target.checked; updateSkeleton(); }
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
      // Estos tres viven en `site.config.json`, no en `tourism-data`.
      var cfg = state.server["site-config"] || {};
      d.domain = (cfg.deploy || {}).domain || "";
      d.email = (cfg.contact || {}).email || "";
      d.phone = (cfg.contact || {}).phone || "";
      d._init = true;
    }

    container.appendChild(el("h2", { text: "Datos del sitio" }));
    container.appendChild(el("p", { class: "hint", text: "Nombre, region, idioma por defecto y centro del mapa." }));

    container.appendChild(textField("Nombre del sitio", d, "name"));
    container.appendChild(textField("Region", d, "region"));
    container.appendChild(textField("Idioma por defecto (ej. es)", d, "defaultLocale"));
    container.appendChild(el("div", { class: "row" }, [
      textField("Latitud del centro", d, "lat"),
      textField("Longitud del centro", d, "lng")
    ]));
    container.appendChild(textField("Zoom del centro (opcional)", d, "zoom"));

    // --- Direccion web y contacto (viven en site.config.json) ---
    // La direccion web NO es solo del paso de publicacion: el BUILD la usa para
    // la URL canonica, las etiquetas para compartir en redes y el sitemap. Si se
    // pidiera recien al publicar, el sitio ya se habria generado con URLs de
    // marcador y habria que reconstruirlo.
    container.appendChild(el("h3", { class: "brand-legend", text: "Dirección y contacto" }));
    container.appendChild(textField("Dirección web del sitio (opcional)", d, "domain"));
    container.appendChild(el("p", { class: "hint", text: "Por ejemplo: turismo.miprovincia.gob.bo. Se usa para el buscador y para que el sitio se vea bien al compartirlo. Podés dejarlo vacío mientras probás en tu computadora." }));
    container.appendChild(el("div", { class: "row" }, [
      textField("Email de contacto (opcional)", d, "email"),
      textField("Teléfono de contacto (opcional)", d, "phone")
    ]));
    container.appendChild(el("p", { class: "hint", text: "Aparecen en el pie del sitio para que un visitante pueda escribirte o llamarte." }));

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
    // El paso escribe en DOS documentos del contrato: nombre/region/centro van a
    // `tourism-data`, mientras que la direccion web y el contacto viven en
    // `site.config`. Se encadenan para que un fallo del segundo (p. ej. un
    // dominio mal escrito) se muestre sin haber perdido lo del primero.
    apiRequest("PUT", "/api/tourism-data/site", { json: payload })
      .then(function (doc) {
        state.server["tourism-data"] = doc;
        // `PUT /api/site-config` exige la seleccion de modulos, asi que se
        // reenvia la actual para no perderla al guardar estos campos.
        var cfgPayload = {
          modules: currentModulesPayload(),
          domain: d.domain || "",
          contact: { email: d.email || "", phone: d.phone || "" }
        };
        return apiRequest("PUT", "/api/site-config", { json: cfgPayload });
      })
      .then(function (cfg) {
        state.server["site-config"] = cfg;
        markDone("site");
        renderOk(document.getElementById("step-panel"), "Sitio guardado.");
      })
      .catch(function (err) { handleStepError(container, err); });
  }

  // --- Paso: Lugares (Req 3.2, 3.4-3.6) ------------------------------------
  function renderPlaces(container) {
    var d = state.draft.place;
    container.appendChild(el("h2", { text: "Lugares" }));
    container.appendChild(el("p", { class: "hint", text: "Agrega lugares turisticos. Podes dar coordenadas o solo una direccion (se geocodifica al generar)." }));

    container.appendChild(textField("Nombre", d, "name"));
    // Categoria: <select> cuando el contrato ya declara categorias (evita
    // duplicados por tipeo); texto libre solo si todavia no hay ninguna.
    var cats = categoryOptions();
    container.appendChild(cats.length
      ? selectField("Categoria", d, "category", cats, "Elegi una categoria")
      : textField("Categoria", d, "category"));
    container.appendChild(el("div", { class: "row" }, [
      textField("Latitud (opcional)", d, "lat"),
      textField("Longitud (opcional)", d, "lng")
    ]));
    container.appendChild(textField("Direccion (opcional)", d, "address"));

    container.appendChild(el("button", {
      class: "btn", text: "Agregar lugar",
      onclick: function () { savePlace(container); }
    }));

    appendEntityList(container, {
      title: "Lugares cargados",
      entity: "places",
      items: (state.server["tourism-data"] || {}).places,
      emptyText: "Todavia no cargaste ningun lugar. Agrega el primero con el formulario de arriba.",
      describe: function (p) {
        var partes = [];
        if (p.category) partes.push(p.category);
        var fotos = (p.images || []).length;
        partes.push(fotos === 1 ? "1 foto" : fotos + " fotos");
        // Señal util: sin coords el lugar no aparece en el mapa hasta geocodificar.
        if (!p.coords) partes.push(p.address ? "sin coords (se geocodifica)" : "sin ubicacion");
        return partes.join("  ·  ");
      },
      fields: [
        { key: "name", label: "Nombre" },
        cats.length
          ? { key: "category", label: "Categoria", type: "select", options: categoryOptions, placeholder: "Elegi una categoria" }
          : { key: "category", label: "Categoria" },
        { key: "address", label: "Direccion" },
        { key: "shortDescription", label: "Descripcion corta" },
        { key: "hours", label: "Horario" }
      ]
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
    container.appendChild(el("h2", { text: "Eventos" }));
    container.appendChild(el("p", { class: "hint", text: "Agrega festividades y eventos con su fecha de inicio." }));

    container.appendChild(textField("Nombre", d, "name"));
    container.appendChild(el("div", { class: "row" }, [
      textField("Fecha de inicio (AAAA-MM-DD)", d, "startDate"),
      textField("Fecha de fin (opcional)", d, "endDate")
    ]));
    // Lugar asociado: se elige por NOMBRE de una lista de lugares ya cargados.
    // Antes habia que tipear el id (slug), que el usuario no conoce.
    var lugares = entityOptions("places");
    container.appendChild(lugares.length
      ? selectField("Lugar asociado (opcional)", d, "placeId", lugares, "Sin lugar asociado")
      : el("p", { class: "hint", text: "Para asociar un lugar a un evento, carga primero los lugares en el paso anterior." }));
    container.appendChild(textareaField("Descripcion (opcional)", d, "description"));

    container.appendChild(el("button", {
      class: "btn", text: "Agregar evento",
      onclick: function () { saveEvent(container); }
    }));

    appendEntityList(container, {
      title: "Eventos cargados",
      entity: "events",
      items: (state.server["tourism-data"] || {}).events,
      emptyText: "Todavia no cargaste ningun evento. Agrega el primero con el formulario de arriba.",
      describe: function (ev) {
        var partes = [];
        if (ev.startDate) partes.push(ev.startDate + (ev.endDate ? " a " + ev.endDate : ""));
        // Se resuelve el nombre del lugar; mostrar el slug no le dice nada al usuario.
        if (ev.placeId) {
          var lugar = ((state.server["tourism-data"] || {}).places || [])
            .filter(function (p) { return p.id === ev.placeId; })[0];
          partes.push("en " + (lugar ? lugar.name : ev.placeId));
        }
        return partes.join("  ·  ");
      },
      fields: [
        { key: "name", label: "Nombre" },
        { key: "startDate", label: "Fecha de inicio (AAAA-MM-DD)" },
        { key: "endDate", label: "Fecha de fin" },
        { key: "placeId", label: "Lugar asociado", type: "select", options: function () { return entityOptions("places"); }, placeholder: "Sin lugar asociado" },
        { key: "description", label: "Descripcion", type: "textarea" }
      ]
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
  //
  // Rediseño del paso: antes era un `<input type=file>` de a un archivo por vez,
  // sin ver nunca lo subido, y para asociar una foto habia que TIPEAR el id del
  // lugar. Ahora hay (a) una zona de arrastre que acepta varios archivos, (b) una
  // galeria con miniaturas de lo ya cargado y a quien pertenece cada foto, y (c)
  // seleccion del destino por NOMBRE del lugar/evento.

  // Inventario de assets del servidor. Se cachea en `state` para que volver al
  // paso no parpadee, y se refresca tras cada alta/baja.
  function refreshAssets(container) {
    return apiRequest("GET", "/api/assets")
      .then(function (res) {
        state.assets = res.assets || [];
        render();
      })
      .catch(function (err) {
        // Ante un fallo hay que salir del estado `null`: si no, el paso se queda
        // en "Cargando galeria..." para siempre (la carga perezosa solo se
        // dispara con `null`) y el usuario no puede ni subir archivos.
        state.assets = [];
        render();
        if (container) handleStepError(document.getElementById("step-panel"), err);
      });
  }

  // Sube una lista de archivos en secuencia, informando el resultado agregado.
  // Se hace secuencial (y no en paralelo) para que la desambiguacion de nombres
  // del backend (`slug-1.jpg`, `slug-2.jpg`) sea determinista.
  function uploadFiles(container, files, target, entityId) {
    var lista = Array.prototype.slice.call(files);
    if (!lista.length) return;

    var subidos = [];
    var fallidos = [];
    var panel = document.getElementById("step-panel");
    var progreso = el("p", { class: "hint", text: "Subiendo 0 de " + lista.length + "..." });
    container.appendChild(progreso);

    var cadena = Promise.resolve();
    lista.forEach(function (file) {
      cadena = cadena.then(function () {
        var fd = new FormData();
        fd.append("file", file);
        if (target) fd.append("target", target);
        if (entityId) fd.append("id", entityId);
        return apiRequest("POST", "/api/assets", { form: fd })
          .then(function (res) {
            subidos.push(res.path);
            if (res.document) {
              if (res.document.places || res.document.events) state.server["tourism-data"] = res.document;
              else state.server["theme-tokens"] = res.document;
            }
          })
          .catch(function (err) {
            var msg = err && err.__wizardError ? err.normalized.cause : "error de red";
            fallidos.push(file.name + ": " + msg);
          })
          .then(function () {
            progreso.textContent = "Subiendo " + (subidos.length + fallidos.length) + " de " + lista.length + "...";
          });
      });
    });

    cadena.then(function () {
      if (subidos.length) markDone("assets");
      return refreshAssets(container);
    }).then(function () {
      panel = document.getElementById("step-panel");
      if (subidos.length) {
        renderOk(panel, subidos.length === 1
          ? "Se subio 1 recurso."
          : "Se subieron " + subidos.length + " recursos.");
      }
      // Los fallos se muestran uno por uno: el usuario necesita saber CUAL
      // archivo fallo y por que, no un "hubo errores" generico.
      if (fallidos.length) {
        renderError(panel, {
          cause: "No se pudieron subir " + fallidos.length + " archivo(s).",
          fix: fallidos.join(" | "),
          doc: ""
        });
      }
    });
  }

  function renderAssets(container) {
    container.appendChild(el("h2", { text: "Recursos (imagenes y logo)" }));
    container.appendChild(el("p", { class: "hint", text: "Arrastra las fotos o elegilas de tu computadora. Podes subir varias a la vez. Formatos: jpg, png, webp, gif, svg, avif (hasta 10 MB cada una)." }));

    // --- Destino de la carga ---
    var d = state.draft.asset || (state.draft.asset = { target: "", id: "" });
    var lugares = entityOptions("places");
    var eventos = entityOptions("events");

    var destinos = [
      { value: "", label: "Sin asociar (solo subir a la galeria)" },
      { value: "logo", label: "Logo de la marca" }
    ];
    if (lugares.length) destinos.push({ value: "place", label: "Foto de un lugar" });
    if (eventos.length) destinos.push({ value: "event", label: "Foto de un evento" });

    // Al cambiar el destino hay que re-renderizar: de `target` depende que
    // aparezca (o no) el selector de lugar/evento de abajo.
    container.appendChild(selectField("Asociar a", d, "target", destinos, null, function () {
      d.id = "";
      render();
    }));

    // El id destino se ELIGE por nombre; solo aparece si el destino lo requiere.
    if (d.target === "place" || d.target === "event") {
      var opciones = d.target === "place" ? lugares : eventos;
      container.appendChild(
        selectField(
          d.target === "place" ? "¿A que lugar?" : "¿A que evento?",
          d, "id", opciones, "Elegi uno"
        )
      );
    }
    if ((d.target === "place" && !lugares.length) || (d.target === "event" && !eventos.length)) {
      container.appendChild(el("p", { class: "hint", text: "Todavia no hay entradas para asociar. Cargalas en los pasos anteriores." }));
    }

    // --- Zona de arrastre + selector de archivos ---
    var fileInput = el("input", {
      type: "file", accept: "image/*", multiple: true, class: "visually-hidden",
      onchange: function (e) {
        uploadFiles(container, e.target.files, d.target, d.id);
        e.target.value = "";
      }
    });

    var dropzone = el("div", {
      class: "dropzone", tabindex: "0", role: "button",
      onclick: function () { fileInput.click(); },
      onkeydown: function (e) {
        // Accesible por teclado: Enter/Espacio abren el selector de archivos.
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); }
      },
      ondragover: function (e) { e.preventDefault(); dropzone.classList.add("is-over"); },
      ondragleave: function () { dropzone.classList.remove("is-over"); },
      ondrop: function (e) {
        e.preventDefault();
        dropzone.classList.remove("is-over");
        if (e.dataTransfer && e.dataTransfer.files) {
          uploadFiles(container, e.dataTransfer.files, d.target, d.id);
        }
      }
    }, [
      el("p", { class: "dropzone-title", text: "Arrastra las imagenes aca" }),
      el("p", { class: "dropzone-hint", text: "o hace clic para elegirlas" })
    ]);

    container.appendChild(dropzone);
    container.appendChild(fileInput);

    // --- Galeria de lo ya cargado ---
    if (state.assets == null) {
      container.appendChild(el("p", { class: "hint", text: "Cargando galeria..." }));
      refreshAssets(container);
      return;
    }

    var galeria = el("div", { class: "entity-section" });
    galeria.appendChild(el("h3", { text: "Galeria (" + state.assets.length + ")" }));

    if (!state.assets.length) {
      galeria.appendChild(el("p", { class: "empty-state", text: "Todavia no subiste ninguna imagen." }));
      container.appendChild(galeria);
      return;
    }

    var grid = el("div", { class: "asset-grid" });
    state.assets.forEach(function (a) {
      var usos = a.usedBy || [];
      grid.appendChild(el("figure", { class: "asset-card" }, [
        el("img", {
          class: "asset-thumb",
          src: "/api/assets/raw/" + encodeURIComponent(a.name),
          alt: a.name, loading: "lazy"
        }),
        el("figcaption", { class: "asset-caption" }, [
          el("span", { class: "asset-name", title: a.name, text: a.name }),
          // Saber a que entrada pertenece cada foto es lo que convierte la
          // galeria en algo revisable (antes no habia forma de saberlo).
          el("span", {
            class: "asset-usage",
            text: usos.length ? "en " + usos.join(", ") : "sin asociar"
          }),
          el("span", { class: "asset-size", text: Math.round(a.bytes / 1024) + " KB" })
        ]),
        el("button", {
          class: "btn btn-danger btn-sm",
          text: "Borrar",
          onclick: function () { deleteAsset(container, a); }
        })
      ]));
    });
    galeria.appendChild(grid);
    container.appendChild(galeria);
  }

  function deleteAsset(container, asset) {
    var usos = asset.usedBy || [];
    // Si la imagen esta en uso se advierte a que entrada afecta antes de borrar.
    var aviso = usos.length
      ? "«" + asset.name + "» se esta usando en: " + usos.join(", ") + ".\nSe quitara de esas entradas.\n\n¿Borrar igual?"
      : "¿Borrar «" + asset.name + "»?";
    if (!window.confirm(aviso)) return;

    apiRequest("DELETE", "/api/assets/" + encodeURIComponent(asset.name))
      .then(function () {
        // El borrado depura las referencias del contrato: hay que recargar el
        // estado para que las fichas dejen de contar esa foto.
        return apiRequest("GET", "/api/state").then(function (st) {
          state.server = st;
          return refreshAssets(container);
        });
      })
      .then(function () {
        renderOk(document.getElementById("step-panel"), "Recurso borrado.");
      })
      .catch(function (err) { handleStepError(container, err); });
  }

  // --- Paso: Q&A (Req 5.1, 5.4) --------------------------------------------
  function renderQA(container) {
    var d = state.draft.qa;
    container.appendChild(el("h2", { text: "Preguntas y respuestas" }));
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
            return refreshQA(container);
          })
          .then(function () {
            renderOk(document.getElementById("step-panel"), "Q&A guardada.");
          })
          .catch(function (err) { handleStepError(container, err); });
      }
    }));

    // --- Lista de lo ya cargado ---
    // El Q&A alimenta al chat del sitio: sin verlo, el usuario no puede detectar
    // duplicados ni respuestas desactualizadas. Se carga una sola vez y se
    // refresca tras cada alta/baja.
    if (state.qa == null) {
      container.appendChild(el("p", { class: "hint", text: "Cargando preguntas..." }));
      refreshQA(container);
      return;
    }

    var section = el("div", { class: "entity-section" });
    section.appendChild(el("h3", { text: "Preguntas cargadas (" + state.qa.length + ")" }));

    if (!state.qa.length) {
      section.appendChild(el("p", { class: "empty-state", text: "Todavia no cargaste ninguna pregunta. El chat del sitio usara estas respuestas." }));
      container.appendChild(section);
      return;
    }

    var ul = el("ul", { class: "entity-list" });
    state.qa.forEach(function (item) {
      ul.appendChild(el("li", { class: "entity-item" }, [
        el("div", { class: "entity-row" }, [
          el("div", { class: "entity-main" }, [
            el("span", { class: "entity-name", text: item.question }),
            el("span", { class: "entity-meta", text: item.answer })
          ]),
          el("div", { class: "entity-actions" }, [
            el("button", {
              class: "btn btn-danger btn-sm", text: "Borrar",
              onclick: function () { deleteQA(container, item); }
            })
          ])
        ])
      ]));
    });
    section.appendChild(ul);
    container.appendChild(section);
  }

  function refreshQA(container) {
    return apiRequest("GET", "/api/qa")
      .then(function (res) {
        state.qa = res.entries || [];
        render();
      })
      .catch(function (err) {
        // Mismo motivo que en `refreshAssets`: salir de `null` para no dejar el
        // paso atascado en "Cargando preguntas...".
        state.qa = [];
        render();
        if (container) handleStepError(document.getElementById("step-panel"), err);
      });
  }

  function deleteQA(container, item) {
    if (!window.confirm("¿Borrar la pregunta «" + item.question + "»?")) return;
    apiRequest("DELETE", "/api/qa/" + item.index)
      .then(function () { return refreshQA(container); })
      .then(function () {
        renderOk(document.getElementById("step-panel"), "Pregunta borrada.");
      })
      .catch(function (err) { handleStepError(container, err); });
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

    // Valores por defecto: sin ellos el selector de color arranca en negro y la
    // vista previa se ve rota antes de que el usuario toque nada.
    if (!d.primary) d.primary = "#1F2933";
    if (!d.background) d.background = "#FFFFFF";
    if (!d.text) d.text = "#1F2933";
    if (!d.accent) d.accent = "#2563EB";
    if (!d.secondary) d.secondary = "#52606D";

    container.appendChild(el("h2", { text: "Marca" }));
    container.appendChild(el("p", { class: "hint", text: "Elegi los colores y las tipografias de tu sitio. Todo lo que cambies se ve al instante en la vista previa de abajo." }));

    // --- Paletas sugeridas ---
    // Un usuario no-tecnico no tiene por que saber armar una paleta accesible
    // desde cero. Estas combinaciones ya estan balanceadas (fondo claro, texto
    // de alto contraste, acento diferenciado del primario) y sirven de punto de
    // partida editable.
    container.appendChild(el("h3", { class: "brand-legend", text: "Paletas sugeridas" }));
    var paletas = el("div", { class: "palette-row" });
    PALETTES.forEach(function (p) {
      var activa = p.colors.primary.toLowerCase() === (d.primary || "").toLowerCase();
      paletas.appendChild(el("button", {
        class: "palette" + (activa ? " is-active" : ""),
        type: "button",
        title: p.name,
        "aria-label": "Aplicar paleta " + p.name,
        onclick: function () {
          Object.keys(p.colors).forEach(function (k) { d[k] = p.colors[k]; });
          render();
        }
      }, [
        el("span", { class: "palette-chips" }, ["primary", "accent", "secondary", "background"].map(function (k) {
          return el("span", { class: "palette-chip", style: "background:" + p.colors[k] });
        })),
        el("span", { class: "palette-name", text: p.name })
      ]));
    });
    container.appendChild(paletas);

    // --- Colores individuales ---
    container.appendChild(el("h3", { class: "brand-legend", text: "Colores" }));
    var grid = el("div", { class: "color-grid" });
    [
      ["primary", "Primario", "Color principal de la marca."],
      ["accent", "Acento", "Botones, enlaces y detalles."],
      ["background", "Fondo", "Fondo general de las paginas."],
      ["text", "Texto", "Color del texto sobre el fondo."],
      ["secondary", "Secundario", "Color de apoyo (opcional)."]
    ].forEach(function (spec) {
      grid.appendChild(colorField(spec[0], spec[1], spec[2], d));
    });
    container.appendChild(grid);

    // --- Tipografia ---
    container.appendChild(el("h3", { class: "brand-legend", text: "Tipografia" }));
    container.appendChild(el("p", { class: "hint", text: "Las familias marcadas como incluidas viajan con tu sitio: se ven igual en cualquier computadora, sin depender de internet." }));

    var duplas = el("div", { class: "pairing-row" });
    FONT_PAIRINGS.forEach(function (par) {
      var activa = par.heading === d.headingFont && par.body === d.bodyFont;
      duplas.appendChild(el("button", {
        class: "pairing" + (activa ? " is-active" : ""), type: "button",
        onclick: function () { d.headingFont = par.heading; d.bodyFont = par.body; render(); }
      }, [
        el("span", { class: "pairing-sample", style: "font-family:" + fontStackOf(par.heading), text: "Aa" }),
        el("span", { class: "pairing-names", text: par.heading + " + " + par.body })
      ]));
    });
    container.appendChild(duplas);

    container.appendChild(el("div", { class: "row" }, [
      fontSelect("Tipografia de titulos", d, "headingFont"),
      fontSelect("Tipografia de cuerpo", d, "bodyFont")
    ]));

    container.appendChild(textField("Tono de voz (opcional)", d, "tone"));

    // --- Vista previa en vivo ---
    // Es lo que convierte este paso de "cinco campos de texto" en una decision
    // informada: el usuario ve la identidad aplicada antes de generar el sitio.
    container.appendChild(el("h3", { class: "brand-legend", text: "Vista previa" }));
    container.appendChild(brandPreview(d));

    container.appendChild(el("button", {
      class: "btn", text: "Guardar marca",
      onclick: function () { saveBrand(container); }
    }));
  }

  // --- Piezas del paso Marca ------------------------------------------------

  // Campo de color: selector nativo + hex escribible, sincronizados en ambos
  // sentidos. El selector solo entiende `#rrggbb`, asi que el texto se valida
  // antes de reflejarlo (si no, tipear "#ab" reseteaba el selector a negro).
  function colorField(key, label, hint, d) {
    var HEX = /^#[0-9a-fA-F]{6}$/;
    var picker = el("input", {
      type: "color", class: "color-swatch", value: HEX.test(d[key] || "") ? d[key] : "#000000",
      "aria-label": label,
      oninput: function (e) { d[key] = e.target.value.toUpperCase(); texto.value = d[key]; repaintPreview(d); }
    });
    var texto = el("input", {
      type: "text", class: "color-hex", value: d[key] || "", spellcheck: "false",
      oninput: function (e) {
        var v = e.target.value.trim();
        d[key] = v;
        if (HEX.test(v)) { picker.value = v; repaintPreview(d); }
      }
    });
    return el("div", { class: "color-field" }, [
      el("label", { text: label }),
      el("div", { class: "color-controls" }, [picker, texto]),
      el("small", { class: "hint", text: hint })
    ]);
  }

  // Desplegable de familias del catalogo. Antes era texto libre: habia que saber
  // de memoria el nombre exacto de una fuente, y cualquier error dejaba al sitio
  // con la tipografia por defecto sin avisar.
  function fontSelect(label, d, key) {
    var sel = el("select", {
      onchange: function (e) { d[key] = e.target.value; render(); }
    });
    FONT_CATALOG.forEach(function (f) {
      var incluida = isSelfHosted(f.name);
      sel.appendChild(el("option", {
        value: f.name,
        text: f.name + (incluida ? "  (incluida)" : "  (del sistema)")
      }));
    });
    sel.value = d[key] || "";
    return el("div", { class: "field" }, [el("label", { text: label }), sel]);
  }

  // Maqueta reducida del sitio con los tokens elegidos. Usa las MISMAS variables
  // CSS que la plantilla (--color-*, --font-*), de modo que lo que se ve aca es
  // lo que se va a generar.
  function brandPreview(d) {
    var box = el("div", { class: "brand-preview", id: "brand-preview" }, [
      el("div", { class: "bp-bar" }, [
        el("span", { class: "bp-brand", text: (state.server["tourism-data"] || {}).site
          ? ((state.server["tourism-data"] || {}).site.name || "Tu sitio") : "Tu sitio" }),
        el("span", { class: "bp-cta", text: "Contacto" })
      ]),
      el("div", { class: "bp-body" }, [
        el("p", { class: "bp-eyebrow", text: "TU REGION" }),
        el("h4", { class: "bp-title", text: "Un titular de ejemplo" }),
        el("p", { class: "bp-text", text: "Asi se va a ver el texto de tu sitio con los colores y las tipografias que elegiste." }),
        el("div", { class: "bp-actions" }, [
          el("span", { class: "bp-btn", text: "Boton principal" }),
          el("span", { class: "bp-btn ghost", text: "Secundario" })
        ])
      ])
    ]);
    applyPreviewVars(box, d);
    return box;
  }

  // Escribe los tokens elegidos como variables CSS sobre el nodo de la preview.
  function applyPreviewVars(box, d) {
    box.style.setProperty("--bp-primary", d.primary || "#1F2933");
    box.style.setProperty("--bp-accent", d.accent || "#2563EB");
    box.style.setProperty("--bp-bg", d.background || "#FFFFFF");
    box.style.setProperty("--bp-text", d.text || "#1F2933");
    box.style.setProperty("--bp-heading", fontStackOf(d.headingFont));
    box.style.setProperty("--bp-body", fontStackOf(d.bodyFont));
  }

  // Repinta sin re-renderizar el paso: al arrastrar el selector de color, un
  // `render()` completo recrearia el input y cortaria el gesto del usuario.
  function repaintPreview(d) {
    var box = document.getElementById("brand-preview");
    if (box) applyPreviewVars(box, d);
    // El esqueleto lateral tambien se recolorea en vivo con el color en edicion,
    // sin esperar a "Guardar marca" (lee el borrador de marca).
    updateSkeleton();
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

    container.appendChild(el("h2", { text: "Portada" }));
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
    container.appendChild(el("h2", { text: "Generar el sitio" }));
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
    container.appendChild(el("h2", { text: "Previsualizar" }));
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
    container.appendChild(el("h2", { text: "Publicar" }));
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

  // Campo <select> enlazado a `obj[key]`. `options` es una lista de
  // {value,label}; se antepone `placeholder` como opcion vacia cuando se indica.
  // `onChanged` es un callback opcional para los selects cuyo valor cambia QUE
  // se muestra despues (p. ej. el destino de un asset decide si aparece el
  // selector de lugar/evento); sin el, el campo dependiente no se dibujaria
  // hasta el siguiente re-render.
  function selectField(labelText, obj, key, options, placeholder, onChanged) {
    var sel = el("select", {
      onchange: function (e) {
        obj[key] = e.target.value;
        if (onChanged) onChanged(e.target.value);
      }
    });
    if (placeholder != null) {
      sel.appendChild(el("option", { value: "", text: placeholder }));
    }
    options.forEach(function (opt) {
      sel.appendChild(el("option", { value: opt.value, text: opt.label }));
    });
    sel.value = obj[key] != null ? obj[key] : "";
    return el("div", { class: "field" }, [el("label", { text: labelText }), sel]);
  }

  // Opciones {value,label} de los Places/Events ya cargados. Sustituyen a los
  // campos donde antes habia que TIPEAR el id (`placeId`, destino de un asset):
  // el usuario elige por nombre y la UI manda el id, que es lo que el usuario
  // no-tecnico no tiene por que conocer.
  function entityOptions(entityKey) {
    var items = (state.server["tourism-data"] || {})[entityKey] || [];
    return items.map(function (it) {
      return { value: it.id, label: it.name || it.id };
    });
  }

  // Categorias declaradas en el contrato, para ofrecerlas como <select> en vez
  // de texto libre (evita que "Naturaleza" y "naturaleza" convivan como dos
  // categorias distintas). Si el contrato aun no declara ninguna, se cae a un
  // campo de texto (ver `renderPlaces`).
  function categoryOptions() {
    var cats = (state.server["tourism-data"] || {}).categories || [];
    return cats.map(function (c) {
      return { value: c.id, label: c.label || c.id };
    });
  }

  // Estado de "que fila esta en edicion", por entidad. Guarda el id abierto y el
  // borrador de sus campos, para que re-renderizar no cierre el formulario.
  var editing = { places: null, events: null };
  var editDraft = {};

  // --- Lista de entidades cargadas, con editar y borrar --------------------
  // Reemplaza al antiguo `appendSavedList` (solo texto): ademas de mostrar lo
  // cargado, permite corregir y dar de baja sin salir del paso. Editar hace PUT
  // y borrar hace DELETE sobre /api/tourism-data/<entity>/<id>, que delegan en
  // `puriq.core.Puriq` (mismo punto de orquestacion que CLI y MCP).
  //
  // `fields` describe los campos editables: [{key, label, type, options}].
  function appendEntityList(container, opts) {
    var items = opts.items || [];
    var section = el("div", { class: "entity-section" });
    section.appendChild(
      el("h3", { text: opts.title + " (" + items.length + ")" })
    );

    // Estado vacio explicito: antes la lista simplemente no se dibujaba y el
    // usuario no sabia si habia guardado o no.
    if (!items.length) {
      section.appendChild(el("p", { class: "empty-state", text: opts.emptyText }));
      container.appendChild(section);
      return;
    }

    var ul = el("ul", { class: "entity-list" });
    items.forEach(function (item) {
      var abierto = editing[opts.entity] === item.id;
      var li = el("li", { class: "entity-item" + (abierto ? " is-editing" : "") });

      li.appendChild(el("div", { class: "entity-row" }, [
        el("div", { class: "entity-main" }, [
          el("span", { class: "entity-name", text: item.name || item.id }),
          el("span", { class: "entity-meta", text: opts.describe(item) })
        ]),
        el("div", { class: "entity-actions" }, [
          el("button", {
            class: "btn btn-ghost btn-sm",
            text: abierto ? "Cancelar" : "Editar",
            onclick: function () {
              editing[opts.entity] = abierto ? null : item.id;
              editDraft = abierto ? {} : JSON.parse(JSON.stringify(item));
              render();
            }
          }),
          el("button", {
            class: "btn btn-danger btn-sm",
            text: "Borrar",
            onclick: function () { deleteEntity(container, opts, item); }
          })
        ])
      ]));

      if (abierto) {
        var form = el("div", { class: "entity-edit" });
        opts.fields.forEach(function (f) {
          if (f.type === "textarea") {
            form.appendChild(textareaField(f.label, editDraft, f.key));
          } else if (f.type === "select") {
            form.appendChild(
              selectField(f.label, editDraft, f.key, f.options(), f.placeholder)
            );
          } else {
            form.appendChild(textField(f.label, editDraft, f.key));
          }
        });
        form.appendChild(el("button", {
          class: "btn", text: "Guardar cambios",
          onclick: function () { saveEntityEdit(container, opts, item); }
        }));
        li.appendChild(form);
      }

      ul.appendChild(li);
    });
    section.appendChild(ul);
    container.appendChild(section);
  }

  function saveEntityEdit(container, opts, item) {
    var payload = {};
    opts.fields.forEach(function (f) {
      var v = editDraft[f.key];
      // Solo se envian los campos con valor: el backend hace merge y los
      // ausentes no se tocan (no se pisa `images` ni nada que no este en el form).
      if (v != null && v !== "") payload[f.key] = v;
    });
    apiRequest("PUT", "/api/tourism-data/" + opts.entity + "/" + encodeURIComponent(item.id), { json: payload })
      .then(function (res) {
        state.server["tourism-data"] = res.document;
        editing[opts.entity] = null;
        editDraft = {};
        render();
        renderOk(document.getElementById("step-panel"), "Cambios guardados.");
      })
      .catch(function (err) { handleStepError(container, err); });
  }

  function deleteEntity(container, opts, item) {
    var nombre = item.name || item.id;
    if (!window.confirm("¿Borrar «" + nombre + "»? Esta accion no se puede deshacer.")) return;
    apiRequest("DELETE", "/api/tourism-data/" + opts.entity + "/" + encodeURIComponent(item.id))
      .then(function (res) {
        state.server["tourism-data"] = res.document;
        editing[opts.entity] = null;
        render();
        var panel = document.getElementById("step-panel");
        var afectados = res.affectedEvents || [];
        // Integridad referencial: al borrar un Place, los Events que lo
        // referenciaban quedan sin `placeId`. Se avisa en vez de callarlo,
        // resolviendo el id a nombre (el slug no le dice nada al usuario).
        var nombresAfectados = afectados.map(function (id) {
          var ev = ((state.server["tourism-data"] || {}).events || [])
            .filter(function (e) { return e.id === id; })[0];
          return ev && ev.name ? ev.name : id;
        });
        renderOk(panel, nombresAfectados.length
          ? "Se borro «" + nombre + "». Eventos que quedaron sin lugar: " + nombresAfectados.join(", ")
          : "Se borro «" + nombre + "».");
      })
      .catch(function (err) { handleStepError(container, err); });
  }

  function toNum(v) {
    var n = Number(v);
    return isNaN(n) ? v : n;
  }

  // --- Estado de "paso completado" para la navegacion lateral --------------
  var doneSteps = {};
  function markDone(id) {
    doneSteps[id] = true;
    renderNav();
    updateProgress();
    updateSkeleton();
  }

  // --- Barra de progreso ----------------------------------------------------
  // Refleja cuantos pasos se completaron sobre el total. El ancho lo anima el
  // CSS via la variable `--pct`.
  function updateProgress() {
    var total = STEPS.length;
    var done = STEPS.filter(function (s) { return doneSteps[s.id]; }).length;
    var pct = Math.round((done / total) * 100);
    var bar = document.getElementById("progress-bar");
    if (bar) bar.style.setProperty("--pct", pct + "%");
  }

  // --- Previsualizador del esqueleto (columna derecha) ----------------------
  // Pinta una maqueta del sitio que se rellena con lo que el usuario carga: la
  // marca tine el esqueleto, las secciones activas de la portada y los modulos
  // aparecen como bloques, y los conteos (lugares, eventos) se muestran reales.
  // Es lo que hace ver el resultado tomando forma mientras se avanza.

  // Modulos EFECTIVOS para el esqueleto: enabled, ordenados por `order`. Se
  // prefiere el borrador del paso Modulos (`state.draft.modules`) sobre el
  // contrato guardado, para que el previsualizador refleje el reorden y los
  // toggles EN VIVO —lo que el usuario esta viendo en el formulario— sin tener
  // que apretar "Guardar" antes. Sin borrador (nunca se entro al paso), se usa
  // lo guardado.
  function effectiveModules() {
    var draft = state.draft.modules;
    if (draft && draft.length) {
      return draft
        .filter(function (r) { return r.enabled; })
        .map(function (r, i) {
          return { key: r.key, order: i + 1, display: r.display };
        });
    }
    var mods = (state.server["site-config"] || {}).modules || {};
    return Object.keys(mods)
      .filter(function (k) { return mods[k] && mods[k].enabled; })
      .map(function (k) { return { key: k, order: mods[k].order || 0, display: mods[k].display }; })
      .sort(function (a, b) { return a.order - b.order; });
  }

  // Colores EFECTIVOS: el borrador del paso Marca (si se toco) sobre lo guardado.
  // Asi elegir una paleta recolorea el esqueleto al instante, sin guardar.
  function effectiveColors() {
    var d = state.draft.brand || {};
    var saved = (state.server["theme-tokens"] || {}).colors || {};
    return {
      primary: d.primary || saved.primary,
      accent: d.accent || saved.accent,
      background: d.background || saved.background,
      text: d.text || saved.text
    };
  }

  function updateSkeleton() {
    var sk = document.getElementById("skeleton");
    if (!sk) return;
    clear(sk);

    var data = state.server["tourism-data"] || {};
    var cfg = state.server["site-config"] || {};
    var site = data.site || {};
    var colors = effectiveColors();

    // Tinta el esqueleto con los colores de marca elegidos (o neutros).
    sk.style.setProperty("--sk-primary", colors.primary || "#0a0a0a");
    sk.style.setProperty("--sk-accent", colors.accent || colors.primary || "#6b7280");
    sk.style.setProperty("--sk-bg", colors.background || "#ffffff");
    sk.style.setProperty("--sk-text", colors.text || "#0a0a0a");

    var activeMods = effectiveModules();
    var landing = (cfg.landing || []).filter(function (s) { return s && s.enabled; })
      .sort(function (a, b) { return (a.order || 0) - (b.order || 0); });
    var places = data.places || [];
    var events = data.events || [];

    var hasAnything = site.name || activeMods.length || landing.length || places.length;
    if (!hasAnything) {
      sk.appendChild(el("div", { class: "sk-empty", html:
        "Tu sitio va a aparecer aca.<br>Empeza cargando los datos." }));
      return;
    }

    // Barra superior del sitio (cromo, no una seccion de contenido).
    sk.appendChild(el("div", { class: "sk-block sk-header" }, [
      el("span", { class: "sk-brand", text: site.name || "Tu sitio" }),
      el("span", { class: "sk-nav" }, [el("i"), el("i"), el("i")]),
      el("span", { class: "sk-cta" })
    ]));

    var chatFloating = activeMods.some(function (m) {
      return m.key === "chatweb" && (m.display || "floating") === "floating";
    });

    // --- Grupo PORTADA: las secciones de la pagina principal, en orden. ---
    var landingTypes = landing.length
      ? landing.map(function (s) { return s.type; })
      : ((cfg.hero || site.name) ? ["hero"] : []);
    if (landingTypes.length) {
      sk.appendChild(skGroup("Portada"));
      landingTypes.forEach(function (type) {
        sk.appendChild(skItem(SK_LABEL[type] || "Seccion", landingShape(type)));
      });
    }

    // --- Grupo SECCIONES: los modulos de contenido, en el orden configurado. ---
    var contentMods = activeMods.filter(function (m) { return m.key !== "chatweb"; });
    if (contentMods.length) {
      sk.appendChild(skGroup("Secciones"));
      contentMods.forEach(function (m) {
        if (m.key === "map") {
          sk.appendChild(skItem("Mapa", el("div", { class: "sk-map" })));
        } else if (m.key === "places") {
          sk.appendChild(skItem("Lugares" + count(places), cardsShape()));
        } else if (m.key === "events") {
          sk.appendChild(skItem("Eventos" + count(events), rowsShape()));
        } else if (m.key === "blog") {
          sk.appendChild(skItem("Noticias", cardsShape()));
        }
      });
    }

    // Pie de pagina, siempre cierra la maqueta.
    sk.appendChild(skItem("Pie de pagina", el("div", { class: "sk-footer" })));

    // Asistente flotante (burbuja) si corresponde.
    if (chatFloating) {
      sk.appendChild(el("div", { class: "sk-fab sk-block", title: "Asistente" }));
    }
  }

  // Nombre legible de cada tipo de seccion de portada, para su etiqueta.
  var SK_LABEL = {
    hero: "Portada", features: "Destacados", stats: "En numeros",
    gallery: "Galeria", testimonials: "Testimonios", faq: "Preguntas frecuentes",
    cta: "Contacto"
  };

  // Sufijo " · N" cuando hay conteo, para mostrar cuanto lleva cargado.
  function count(arr) { return arr && arr.length ? " · " + arr.length : ""; }

  // Envuelve una forma con su etiqueta arriba, para que cada seccion del
  // esqueleto se identifique de forma consistente (antes solo algunas la tenian).
  function skItem(label, shape) {
    return el("div", { class: "sk-item" }, [
      el("span", { class: "sk-label", text: label }),
      shape
    ]);
  }

  // Encabezado de grupo (Portada / Secciones): separa visualmente los dos
  // grandes bloques del sitio para que se entienda como se estructura.
  function skGroup(text) {
    return el("div", { class: "sk-group", text: text });
  }

  // Forma (sin etiqueta) de una seccion de portada segun su tipo.
  function landingShape(type) {
    if (type === "hero") {
      return el("div", { class: "sk-block sk-hero" }, [
        el("span", { class: "sk-line lg" }),
        el("span", { class: "sk-line sm" }),
        el("span", { class: "sk-pill" })
      ]);
    }
    if (type === "stats") {
      return el("div", { class: "sk-block sk-stats" }, [el("i"), el("i"), el("i")]);
    }
    if (type === "gallery") {
      return el("div", { class: "sk-block sk-gallery" }, [el("b"), el("b"), el("b"), el("b")]);
    }
    if (type === "cta") {
      return el("div", { class: "sk-block sk-cta-band" }, [el("i")]);
    }
    if (type === "faq" || type === "testimonials") {
      return rowsShape();
    }
    // features (default): tarjetas, la primera destacada.
    return el("div", { class: "sk-block sk-cards" }, [
      el("div", { class: "sk-card feat" }), el("div", { class: "sk-card" }), el("div", { class: "sk-card" })
    ]);
  }

  // Cuadricula de tarjetas (destacados, lugares, noticias).
  function cardsShape() {
    return el("div", { class: "sk-block sk-cards" }, [
      el("div", { class: "sk-card" }), el("div", { class: "sk-card" }), el("div", { class: "sk-card" })
    ]);
  }
  // Lista de filas (eventos, FAQ, testimonios).
  function rowsShape() {
    return el("div", { class: "sk-block sk-rows" }, [el("i"), el("i"), el("i")]);
  }

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
      // El indicador es un radio: vacio (pendiente), punto central (activo, lo
      // dibuja el CSS) o check (hecho). Ya no lleva el numero del paso.
      var btn = el("button", {
        class: cls.trim(),
        "aria-current": idx === state.current ? "step" : null,
        onclick: function () { goTo(idx); }
      }, [
        el("span", { class: "dot", text: doneSteps[step.id] ? "\u2713" : "" }),
        el("span", { class: "step-icon", html: stepIconSvg(step.id) }),
        document.createTextNode(step.label)
      ]);
      nav.appendChild(btn);
    });
  }

  function render() {
    var step = STEPS[state.current];
    var panel = document.getElementById("step-panel");
    clear(panel);
    step.render(panel);
    // Transicion de entrada del panel: se reinicia la animacion quitando y
    // re-agregando la clase (forzando reflow) en cada cambio de paso.
    panel.classList.remove("is-entering");
    void panel.offsetWidth;
    panel.classList.add("is-entering");
    // Cabecera del paso en la barra lateral (patron de la referencia).
    document.getElementById("step-title").textContent = step.title || step.label;
    document.getElementById("step-desc").textContent = step.desc || "";
    document.getElementById("step-indicator").textContent =
      "Paso " + (state.current + 1) + " de " + STEPS.length;
    updateProgress();
    updateSkeleton();
    document.getElementById("btn-prev").disabled = state.current === 0;
    document.getElementById("btn-next").disabled = state.current === STEPS.length - 1;
    renderNav();
  }

  function goTo(idx) {
    if (idx < 0 || idx >= STEPS.length) return;
    state.current = idx;
    render();
  }

  // Divisor arrastrable del previsualizador: ajusta el ancho de la columna
  // derecha y lo recuerda entre sesiones. El ancho se escribe en `--preview-w`,
  // que la cuadricula del `.app` consume; se acota a un rango razonable para que
  // ni el preview ni el area de trabajo desaparezcan.
  var PREVIEW_MIN = 340;
  var PREVIEW_MAX = 760;
  function setupPreviewResize() {
    var handle = document.querySelector(".preview-resize");
    var app = document.querySelector(".app");
    if (!handle || !app) return;

    var guardado = localStorage.getItem("puriq-preview-w");
    if (guardado) document.documentElement.style.setProperty("--preview-w", guardado);

    var dragging = false;
    function onMove(e) {
      if (!dragging) return;
      var w = window.innerWidth - e.clientX;
      w = Math.max(PREVIEW_MIN, Math.min(PREVIEW_MAX, w));
      document.documentElement.style.setProperty("--preview-w", w + "px");
    }
    function onUp() {
      if (!dragging) return;
      dragging = false;
      app.classList.remove("is-resizing");
      localStorage.setItem(
        "puriq-preview-w",
        getComputedStyle(document.documentElement).getPropertyValue("--preview-w").trim()
      );
    }
    handle.addEventListener("mousedown", function (e) {
      dragging = true;
      app.classList.add("is-resizing");
      e.preventDefault();
    });
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }

  // ========================================================================
  // Chat_Panel: chat conversacional con preview en vivo (Pieza 6, Req 7)
  // ========================================================================
  // Panel de chat vanilla montado junto al Live_Preview. Reutiliza las mismas
  // piezas que el resto del wizard: `apiRequest` para el fetch y su
  // normalizacion de errores, `state.server` como fuente del preview y
  // `updateSkeleton` para repintarlo. No duplica el renderizado del esqueleto.
  //
  // Estado propio del panel: `pending` evita envios concurrentes mientras un
  // turno esta en curso (Req 7.4). Se nombra `chatState` para no confundir con
  // la variable local `chat` del paso Modulos (que es de otro alcance).
  // `attachments` guarda los File binarios adjuntos al turno en curso (imagenes
  // y PDFs, Req 6.1); se vacia tras enviar.
  var chatState = { pending: false, attachments: [] };

  // Referencias de assets a adjuntar al turno como CONTEXTO TEXTUAL (Req 8.1).
  // El chat es text-only: el panel NO sube binarios por /api/chat; solo puede
  // referenciar rutas relativas bajo `assets/` que ya subio el flujo de assets
  // existente (drag & drop -> POST /api/assets, cacheado en `state.assets`).
  //
  // Hook preparado: en esta fase se envia [] por defecto para priorizar que el
  // chat de texto funcione end-to-end. Para poblar las referencias desde el
  // inventario de assets ya subidos, descomentar el bloque de abajo (usa el
  // mismo `state.assets` que llena el paso Recursos via GET /api/assets):
  //
  //   if (state.assets && state.assets.length) {
  //     return state.assets.map(function (a) {
  //       return a.path || (a.name ? "assets/" + a.name : null);
  //     }).filter(Boolean);
  //   }
  function chatAttachedFiles() {
    return [];
  }

  // Agrega un mensaje al historial visible de la conversacion (Req 7.2).
  // `attachNames` (opcional) lista los binarios adjuntos al turno para que el
  // usuario vea que envio junto al texto.
  function appendChatMessage(role, text, attachNames) {
    var history = document.getElementById("chat-history");
    if (!history) return;
    var children = [
      el("span", { class: "chat-role", text: role === "user" ? "Vos" : "Puriq" })
    ];
    if (text) children.push(el("p", { class: "chat-bubble", text: text }));
    if (attachNames && attachNames.length) {
      children.push(el("span", {
        class: "chat-msg-files",
        text: "Adjuntos: " + attachNames.join(", ")
      }));
    }
    history.appendChild(el("div", { class: "chat-msg chat-" + role }, children));
    history.scrollTop = history.scrollHeight;
  }

  // Indicador "en curso" + deshabilitar el envio mientras el fetch esta
  // pendiente (Req 7.4).
  function setChatPending(pending) {
    chatState.pending = pending;
    var send = document.getElementById("chat-send");
    var input = document.getElementById("chat-input");
    var attach = document.getElementById("chat-attach");
    var status = document.getElementById("chat-status");
    if (send) send.disabled = pending;
    if (input) input.disabled = pending;
    if (attach) attach.disabled = pending;
    if (status) {
      status.textContent = pending ? "Puriq esta escribiendo\u2026" : "";
      status.hidden = !pending;
    }
  }

  // --- Adjuntos binarios del turno (Req 6.1) -------------------------------
  // El adjuntador reutiliza el patron de carga del paso Recursos: un <input
  // type=file> disparado por un boton y una zona de arrastre. A diferencia de
  // /api/assets (que SUBE cada archivo al soltarlo), aca los File se ACUMULAN en
  // `chatState.attachments` y viajan con el turno como `binarios` del
  // multipart; se vacian tras enviar.
  function addChatAttachments(files) {
    var lista = Array.prototype.slice.call(files || []);
    if (!lista.length) return;
    lista.forEach(function (f) { chatState.attachments.push(f); });
    renderChatAttachments();
  }

  function removeChatAttachment(index) {
    chatState.attachments.splice(index, 1);
    renderChatAttachments();
  }

  function clearChatAttachments() {
    chatState.attachments = [];
    renderChatAttachments();
  }

  // Redibuja los chips de adjuntos desde `chatState.attachments`. Cada chip
  // muestra el nombre del archivo y un boton para quitarlo antes de enviar.
  function renderChatAttachments() {
    var box = document.getElementById("chat-attachments");
    if (!box) return;
    clear(box);
    if (!chatState.attachments.length) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    chatState.attachments.forEach(function (file, idx) {
      box.appendChild(el("span", { class: "chat-chip", title: file.name }, [
        el("span", { class: "chat-chip-name", text: file.name }),
        el("button", {
          type: "button", class: "chat-chip-remove",
          "aria-label": "Quitar " + file.name,
          onclick: function () { removeChatAttachment(idx); }
        }, ["\u00d7"])
      ]));
    });
  }

  function clearChatError() {
    var box = document.getElementById("chat-error");
    if (box) { clear(box); box.hidden = true; }
  }

  // Muestra el error accionable normalizado sin bloquear envios posteriores
  // (Req 7.5). Reutiliza la misma normalizacion {cause, fix, doc} que el resto
  // del wizard (apiRequest ya adjunta `normalized` a los errores del servidor).
  function renderChatError(err) {
    var box = document.getElementById("chat-error");
    if (!box) return;
    var normalized = err && err.__wizardError ? err.normalized : {
      cause: "No se pudo contactar al servidor.",
      fix: "Verifica que el asistente siga corriendo y reintenta.",
      doc: ""
    };
    clear(box);
    box.appendChild(el("p", { class: "cause", text: normalized.cause }));
    if (normalized.fix) box.appendChild(el("p", { class: "fix", text: normalized.fix }));
    if (normalized.doc) box.appendChild(el("p", { class: "doc", text: normalized.doc }));
    box.hidden = false;
  }

  // Vuelca el `estado` de la Chat_Response (los tres documentos del contrato) en
  // `state.server` y repinta el preview con la MISMA funcion updateSkeleton
  // (Req 7.3). `estado` es la salida de get_state: claves de contrato
  // (`tourism-data`/`site-config`/`theme-tokens`) mas `missing`.
  function applyChatEstado(estado) {
    if (!estado || typeof estado !== "object") return;
    ["tourism-data", "site-config", "theme-tokens"].forEach(function (key) {
      if (estado[key] && typeof estado[key] === "object") {
        state.server[key] = estado[key];
      }
    });
    updateSkeleton();
  }

  // Envia el mensaje al Chat_Endpoint con el patron de fetch existente y
  // renderiza el turno (Req 7.2, 7.3).
  // Arma el multipart del turno cuando hay binarios adjuntos (Req 6.1):
  // `mensaje` (texto), `archivos` (referencias, campos repetidos) y `binarios`
  // (los File adjuntos, campos repetidos). No fija Content-Type: el navegador lo
  // pone con el boundary correcto (igual que el drag & drop de /api/assets).
  function chatFormData(mensaje, attachments) {
    var fd = new FormData();
    fd.append("mensaje", mensaje);
    chatAttachedFiles().forEach(function (ref) { fd.append("archivos", ref); });
    attachments.forEach(function (file) { fd.append("binarios", file, file.name); });
    return fd;
  }

  function sendChatMessage() {
    if (chatState.pending) return;
    var input = document.getElementById("chat-input");
    if (!input) return;
    var mensaje = (input.value || "").trim();
    // Un turno necesita texto o al menos un binario adjunto para tener sentido.
    var attachments = chatState.attachments.slice();
    if (!mensaje && !attachments.length) return;

    var attachNames = attachments.map(function (f) { return f.name; });
    appendChatMessage("user", mensaje, attachNames);
    input.value = "";
    // Los adjuntos ya viajan en este turno: se limpian de inmediato (como el
    // texto del input) para no reenviarlos por accidente en el siguiente.
    clearChatAttachments();
    clearChatError();
    setChatPending(true);

    // Con binarios se envia multipart/form-data reutilizando la rama `form` de
    // apiRequest (misma normalizacion de errores {causa,accion}/{documento,...});
    // sin binarios se mantiene el POST JSON del Hito 2, intacto (Req 6.3).
    var opts = attachments.length
      ? { form: chatFormData(mensaje, attachments) }
      : { json: { mensaje: mensaje, archivos: chatAttachedFiles() } };

    apiRequest("POST", "/api/chat", opts)
      .then(function (res) {
        appendChatMessage("assistant", (res && res.respuesta) || "(sin respuesta)");
        applyChatEstado(res && res.estado);
      })
      .catch(function (err) {
        renderChatError(err);
      })
      // Se ejecuta pase lo que pase (exito o error): rehabilita el envio para no
      // bloquear los turnos siguientes (Req 7.5).
      .then(function () {
        setChatPending(false);
        input.focus();
      });
  }

  function setupChat() {
    var form = document.getElementById("chat-form");
    var input = document.getElementById("chat-input");
    if (!form) return;
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      sendChatMessage();
    });
    // Enter envia; Shift+Enter agrega un salto de linea (comodo para textarea).
    if (input) {
      input.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          sendChatMessage();
        }
      });
    }

    // --- Adjuntar binarios (Req 6.1): boton + input file + drag & drop ---
    // Reutiliza el mismo patron que el paso Recursos (dropzone -> input file):
    // el boton dispara el selector nativo y el arrastre sobre el panel acepta
    // archivos soltados. Ambos caminos acumulan en chatState.attachments.
    var attachBtn = document.getElementById("chat-attach");
    var fileInput = document.getElementById("chat-file-input");
    if (attachBtn && fileInput) {
      attachBtn.addEventListener("click", function () {
        if (!chatState.pending) fileInput.click();
      });
      fileInput.addEventListener("change", function (e) {
        addChatAttachments(e.target.files);
        e.target.value = ""; // permite re-elegir el mismo archivo
      });
    }

    var panel = document.getElementById("chat-panel");
    if (panel) {
      panel.addEventListener("dragover", function (e) {
        if (chatState.pending) return;
        e.preventDefault();
        panel.classList.add("is-drop");
      });
      panel.addEventListener("dragleave", function (e) {
        // Solo se apaga al salir del panel, no al pasar entre sus hijos.
        if (e.target === panel) panel.classList.remove("is-drop");
      });
      panel.addEventListener("drop", function (e) {
        e.preventDefault();
        panel.classList.remove("is-drop");
        if (chatState.pending) return;
        if (e.dataTransfer && e.dataTransfer.files) {
          addChatAttachments(e.dataTransfer.files);
        }
      });
    }
  }

  function init() {
    document.getElementById("btn-prev").addEventListener("click", function () { goTo(state.current - 1); });
    document.getElementById("btn-next").addEventListener("click", function () { goTo(state.current + 1); });
    setupPreviewResize();
    setupChat();

    // Catalogo tipografico: se pide una sola vez y se inyectan los `@font-face`
    // para que la vista previa del paso Marca muestre la tipografia REAL. Si
    // falla, la preview cae a las fuentes del sistema (no bloquea el wizard).
    apiRequest("GET", "/api/fonts")
      .then(function (res) {
        state.fontFiles = res.files || [];
        injectFontFaces(state.fontFiles);
      })
      .catch(function () { state.fontFiles = []; });

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

  // Declara en el propio wizard las fuentes que sirve `/fonts`, para que la
  // vista previa de Marca se vea con la tipografia que realmente va a llevar el
  // sitio y no con una aproximacion del sistema.
  function injectFontFaces(files) {
    if (!files || !files.length) return;
    var reglas = files.map(function (archivo) {
      var m = /^(.+)-(var|\d+)\.woff2$/.exec(archivo);
      if (!m) return "";
      var entry = FONT_CATALOG.filter(function (f) { return f.slug === m[1]; })[0];
      if (!entry) return "";
      // Una fuente variable cubre todo el rango con un solo archivo.
      var peso = m[2] === "var" ? "100 900" : m[2];
      return '@font-face{font-family:"' + entry.name + '";font-style:normal;' +
        "font-weight:" + peso + ";font-display:swap;" +
        'src:local("' + entry.name + '"),url("/fonts/' + archivo + '") format("woff2");}';
    }).filter(Boolean).join("\n");
    if (!reglas) return;
    var style = document.createElement("style");
    style.textContent = reglas;
    document.head.appendChild(style);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
