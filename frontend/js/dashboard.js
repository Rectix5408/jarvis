// SPDX-License-Identifier: Apache-2.0; added for this fork, 2026-10-04.
import * as THREE from "three";
import { OrbitControls } from "/static/vendor/three/OrbitControls.js";

const $ = (id) => document.getElementById(id);
try {
  document.body.classList.toggle(
    "light",
    localStorage.getItem("jarvis_theme") === "light",
  );
} catch (_) {}
const state = {
  files: [],
  groups: [],
  selected: null,
  mode: "network",
  paused: matchMedia("(prefers-reduced-motion: reduce)").matches,
  authenticated: false,
};
const token = () =>
  localStorage.getItem("jarvis_chat_token") ||
  localStorage.getItem("jarvis_token") ||
  localStorage.getItem("jarvis_uc_token") ||
  "";
function icons() {
  window.lucide?.createIcons();
}
window.addEventListener("load", icons);
async function api(path, options = {}) {
  const credential = token();
  if (!credential)
    throw Object.assign(new Error("Bitte zuerst anmelden."), { status: 401 });
  const response = await fetch(path, {
    ...options,
    headers: { Authorization: `Bearer ${credential}`, ...options.headers },
    signal: AbortSignal.timeout(20000),
  });
  if (!response.ok)
    throw Object.assign(
      new Error(
        response.status === 401
          ? "Sitzung abgelaufen. Bitte anmelden."
          : response.status === 403
            ? "Keine Berechtigung."
            : "Dienst nicht erreichbar.",
      ),
      { status: response.status },
    );
  return response.json();
}
function failure(error) {
  $("message").textContent = error.message;
  if (error.status === 401) {
    state.authenticated = false;
    state.files = [];
    state.groups = [];
    $("username").textContent = "";
    $("model").textContent = "Nicht geladen";
    $("model-status").textContent = "Status unbekannt";
    $("model-status").dataset.status = "unknown";
    $("cpu").textContent = "--";
    $("cpu-meter").value = 0;
    $("profile").replaceChildren(new Option("Bitte anmelden", ""));
    $("profile").disabled = true;
    $("settings").hidden = true;
    $("file-count").textContent = "--";
    $("group-count").textContent = "--";
    $("login").hidden = false;
    $("connection").textContent = "Nicht angemeldet";
    document.body.dataset.connected = "false";
    $("chat-dialog").close();
    $("chat-frame").removeAttribute("src");
    state.selected = null;
    renderFiles();
    buildGraph();
    select(null);
  }
}

let scene,
  renderer,
  camera,
  controls,
  graph,
  root,
  rings = [],
  pickable = [],
  renderNodes = [];
let drawn = 0,
  angle = 0;
const raycaster = new THREE.Raycaster(),
  pointer = new THREE.Vector2();
function initScene() {
  try {
    scene = new THREE.Scene();
    scene.background = new THREE.Color(
      getComputedStyle(document.body).getPropertyValue("--bg-primary").trim(),
    );
    camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
    camera.position.set(0, 2, 14);
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    $("scene").append(renderer.domElement);
    renderer.domElement.setAttribute(
      "aria-label",
      "Dreidimensionales Wissensnetz",
    );
    controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.enablePan = false;
    controls.minDistance = 5;
    controls.maxDistance = 24;
    scene.add(new THREE.AmbientLight(0xffffff, 2));
    const light = new THREE.DirectionalLight(0xffffff, 3);
    light.position.set(3, 5, 5);
    scene.add(light);
    new ResizeObserver(() => {
      const { width, height } = $("scene").getBoundingClientRect();
      renderer.setSize(width, height);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    }).observe($("scene"));
    let down;
    renderer.domElement.addEventListener("pointerdown", (e) => {
      down = [e.clientX, e.clientY];
    });
    renderer.domElement.addEventListener("pointerup", (e) => {
      if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 6)
        return;
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.set(
        ((e.clientX - rect.left) / rect.width) * 2 - 1,
        (-(e.clientY - rect.top) / rect.height) * 2 + 1,
      );
      raycaster.setFromCamera(pointer, camera);
      const hit = raycaster.intersectObjects(
        pickable.filter((object) => object.visible),
      )[0];
      if (hit) select(hit.object.userData.item || null);
    });
    renderer.domElement.addEventListener("webglcontextlost", (e) => {
      e.preventDefault();
      $("scene-error").hidden = false;
    });
    const clock = new THREE.Clock();
    renderer.setAnimationLoop(() => {
      const delta = Math.min(clock.getDelta(), 0.05);
      if (!state.paused && !document.hidden) {
        angle += delta * 0.09;
        graph.rotation.y = angle;
        rings.forEach((ring, i) => {
          ring.rotation.z += delta * 0.12 * (i % 2 ? -1 : 1);
        });
      }
      controls.update();
      renderer.render(scene, camera);
      $("scene").dataset.frames = String(++drawn);
    });
  } catch (error) {
    $("scene-error").hidden = false;
    console.warn("Dashboard 3D unavailable", error);
  }
}
function disposeGraph() {
  if (!graph) return;
  scene.remove(graph);
  graph.traverse((object) => {
    object.geometry?.dispose();
    if (Array.isArray(object.material))
      object.material.forEach((m) => m.dispose());
    else object.material?.dispose();
  });
}
function node(item, radius, color, position) {
  const mesh = new THREE.Mesh(
    new THREE.SphereGeometry(radius, 16, 12),
    new THREE.MeshStandardMaterial({
      color,
      emissive: color,
      emissiveIntensity: 0.4,
    }),
  );
  mesh.position.copy(position);
  mesh.userData.item = item;
  graph.add(mesh);
  pickable.push(mesh);
  return mesh;
}
function edge(a, b, color = "#426665") {
  graph.add(
    new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([a, b]),
      new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.6 }),
    ),
  );
}
function buildGraph() {
  if (!scene) return;
  disposeGraph();
  graph = new THREE.Group();
  scene.add(graph);
  pickable = [];
  renderNodes = [];
  rings = [];
  root = node(null, 0.43, "#57ddd1", new THREE.Vector3());
  for (let i = 0; i < 3; i++) {
    const ring = new THREE.Mesh(
      new THREE.TorusGeometry(0.9 + i * 0.22, 0.012, 8, 100),
      new THREE.MeshBasicMaterial({ color: i === 1 ? "#efb36f" : "#57ddd1" }),
    );
    ring.rotation.set(i * 0.7, i * 0.9, 0.3);
    graph.add(ring);
    rings.push(ring);
  }
  const positions = new Map();
  state.groups.forEach((group, i) => {
    const theta = i * 2.39996;
    const pos = new THREE.Vector3(
      Math.cos(theta) * 2.6,
      Math.sin(theta) * 2,
      Math.sin(i * 1.7) * 1.6,
    );
    positions.set(group.id, pos);
    node({ ...group, kind: "group" }, 0.17, "#efb36f", pos);
    edge(new THREE.Vector3(), pos);
  });
  // Only geometry is sampled for browser performance; the complete file list remains searchable.
  const sample = state.files.filter((file) => matches(file)).slice(0, 350);
  sample.forEach((file, i) => {
    const theta = i * 2.39996,
      y = 1 - (2 * (i + 0.5)) / Math.max(sample.length, 1);
    const r = Math.sqrt(1 - y * y),
      radius = 4.2 + (i % 3) * 0.24;
    const pos = new THREE.Vector3(
      Math.cos(theta) * r * radius,
      y * radius,
      Math.sin(theta) * r * radius,
    );
    const mesh = node(
      { ...file, kind: "file" },
      0.065,
      document.body.classList.contains("light") ? "#3f7771" : "#c5e9e5",
      pos,
    );
    renderNodes.push(mesh);
    file.groups.forEach((group) => {
      if (positions.has(group.id)) edge(pos, positions.get(group.id));
    });
  });
  graph.rotation.y = angle;
  $("scene").dataset.nodes = String(sample.length);
  if (state.mode === "core") setMode("core");
}
function setMode(mode) {
  const changed = state.mode !== mode;
  state.mode = mode;
  $("network-mode").setAttribute("aria-pressed", String(mode === "network"));
  $("core-mode").setAttribute("aria-pressed", String(mode === "core"));
  if (graph)
    graph.children.forEach((object) => {
      object.visible =
        mode === "network" || object === root || rings.includes(object);
    });
  if (changed && camera) {
    camera.position.set(0, mode === "core" ? 1 : 2, mode === "core" ? 6 : 14);
    controls.target.set(0, 0, 0);
  }
}
function select(item) {
  state.selected = item;
  $("selected-type").textContent =
    item?.kind === "group" ? "GRUPPE" : item ? "DATEI" : "WORKSPACE";
  $("selected-name").textContent = item?.name || "JARVIS";
  $("selected-info").textContent =
    item?.kind === "file"
      ? item.groups.map((g) => g.name).join(" / ")
      : item?.kind === "group"
        ? `${state.files.filter((f) => f.groups.some((g) => g.id === item.id)).length} Dateien`
        : state.authenticated
          ? `${state.files.length} Dateien / ${state.groups.length} Gruppen`
          : "Keine Wissensdaten geladen";
  for (const mesh of renderNodes)
    mesh.scale.setScalar(item?.path === mesh.userData.item.path ? 2 : 1);
  for (const button of $("files").querySelectorAll("button"))
    button.setAttribute(
      "aria-pressed",
      String(button.dataset.path === item?.path),
    );
}
function matches(file) {
  const query = $("search").value.trim().toLocaleLowerCase("de");
  return `${file.name} ${file.groups.map((g) => g.name).join(" ")}`
    .toLocaleLowerCase("de")
    .includes(query);
}
function renderFiles() {
  $("files").replaceChildren();
  const files = state.files.filter(matches);
  for (const file of files) {
    const li = document.createElement("li"),
      button = document.createElement("button");
    button.dataset.path = file.path;
    button.setAttribute(
      "aria-pressed",
      String(file.path === state.selected?.path),
    );
    const name = document.createElement("span");
    name.className = "filename";
    name.textContent = file.name;
    const group = document.createElement("span");
    group.className = "groups";
    group.textContent = file.groups.map((g) => g.name).join(" / ");
    button.append(name, group);
    button.addEventListener("click", () => select({ ...file, kind: "file" }));
    li.append(button);
    $("files").append(li);
  }
  $("files-empty").hidden = files.length > 0;
  $("files-empty").textContent = state.authenticated
    ? "Keine Dateien in diesem Bereich."
    : "Keine Wissensdaten geladen.";
}
let loading = false;
async function refresh() {
  if (loading) return;
  loading = true;
  $("refresh").disabled = true;
  try {
    const me = await api("/api/me");
    state.authenticated = true;
    $("username").textContent = me.username;
    $("settings").hidden = !me.is_admin;
    $("login").hidden = true;
    $("message").textContent = "";
    $("connection").textContent = "Backend verbunden";
    document.body.dataset.connected = "true";
    const results = await Promise.allSettled(
      [
        "/api/wissen/files",
        "/api/llm/profiles",
        "/api/cpu",
        "/api/llm/active-status",
      ].map((path) => api(path)),
    );
    // Expired sessions erase previously displayed scoped data before processing other results.
    const expired = results.find(
      (r) => r.status === "rejected" && r.reason.status === 401,
    );
    if (expired) {
      failure(expired.reason);
      return;
    }
    const [files, profiles, cpu, status] = results;
    if (files.status === "fulfilled") {
      state.files = files.value.files || [];
      state.groups = [
        ...new Map(
          state.files.flatMap((f) => f.groups).map((g) => [g.id, g]),
        ).values(),
      ];
      $("file-count").textContent = state.files.length;
      $("group-count").textContent = state.groups.length;
      renderFiles();
      buildGraph();
      select(null);
    } else {
      state.files = [];
      state.groups = [];
      renderFiles();
      buildGraph();
      select(null);
      $("file-count").textContent = "--";
      $("group-count").textContent = "--";
    }
    if (profiles.status === "fulfilled") {
      const data = profiles.value;
      $("profile").replaceChildren();
      for (const profile of data.profiles) {
        const option = new Option(
          profile.name,
          profile.id,
          false,
          profile.id === data.active_id,
        );
        option.disabled = Boolean(profile.locked);
        $("profile").append(option);
      }
      $("profile").disabled = !data.profiles.length;
      const active = data.profiles.find((p) => p.id === data.active_id);
      $("model").textContent =
        active?.model || active?.name || "Kein aktives Profil";
    } else {
      $("profile").disabled = true;
      $("model").textContent = "Nicht verfuegbar";
    }
    if (cpu.status === "fulfilled" && typeof cpu.value.cpu === "number") {
      $("cpu").textContent = `${Math.round(cpu.value.cpu)} %`;
      $("cpu-meter").value = cpu.value.cpu;
    } else {
      $("cpu").textContent = "--";
      $("cpu-meter").value = 0;
    }
    $("model-status").textContent =
      status.status === "fulfilled"
        ? {
            ok: "Modell erreichbar",
            degraded: "Modell fehlt am Server",
            down: "Modell nicht erreichbar",
          }[status.value.status] || "Status unbekannt"
        : "Status nicht verfuegbar";
    $("model-status").dataset.status =
      status.status === "fulfilled" ? status.value.status : "unknown";
    const failed = results.filter((r) => r.status === "rejected");
    if (failed.length)
      $("message").textContent =
        `${failed.length} Datenquellen nicht verfuegbar. ${failed[0].reason.message}`;
  } catch (error) {
    failure(error);
    if (error.status !== 401) {
      $("connection").textContent = "Verbindung unterbrochen";
      document.body.dataset.connected = "false";
    }
  } finally {
    loading = false;
    $("refresh").disabled = false;
  }
}
$("profile").addEventListener("change", async () => {
  $("profile").disabled = true;
  let rejected;
  try {
    await api(
      `/api/llm/profiles/${encodeURIComponent($("profile").value)}/activate`,
      { method: "POST" },
    );
  } catch (error) {
    rejected = error;
    failure(error);
  }
  await refresh();
  if (rejected) failure(rejected);
});
$("search").addEventListener("input", () => {
  renderFiles();
  buildGraph();
  select(null);
});
$("refresh").addEventListener("click", refresh);
$("network-mode").addEventListener("click", () => setMode("network"));
$("core-mode").addEventListener("click", () => setMode("core"));
function pauseLabel() {
  $("pause").setAttribute("aria-pressed", String(state.paused));
  $("pause").title = state.paused
    ? "Animation fortsetzen"
    : "Animation pausieren";
  $("pause").setAttribute("aria-label", $("pause").title);
  $("pause").replaceChildren();
  const icon = document.createElement("i");
  icon.dataset.lucide = state.paused ? "play" : "pause";
  $("pause").append(icon);
  icons();
}
$("pause").addEventListener("click", () => {
  state.paused = !state.paused;
  pauseLabel();
});
$("reset").addEventListener("click", () => {
  camera?.position.set(0, 2, 14);
  controls?.target.set(0, 0, 0);
  angle = 0;
  if (graph) graph.rotation.y = 0;
});
function openChat() {
  if (!state.authenticated) {
    $("message").textContent = "Bitte zuerst anmelden.";
    $("login").hidden = false;
    return;
  }
  // Reuse the existing chat, including its profile ACLs, tool permissions and microphone flow.
  if (!$("chat-frame").getAttribute("src")) $("chat-frame").src = "/chat";
  $("chat-dialog").showModal();
}
$("open-chat").addEventListener("click", openChat);
$("top-chat").addEventListener("click", openChat);
$("close-chat").addEventListener("click", () => $("chat-dialog").close());
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refresh();
});
window.addEventListener("storage", (event) => {
  if (
    ["jarvis_token", "jarvis_chat_token", "jarvis_uc_token"].includes(
      event.key,
    ) ||
    event.key === null
  )
    refresh();
});
function updateClock() {
  $("clock").textContent = new Date().toLocaleTimeString("de-DE", {
    hour: "2-digit",
    minute: "2-digit",
  });
}
function applySceneTheme() {
  const light = document.body.classList.contains("light");
  if (scene)
    scene.background = new THREE.Color(
      getComputedStyle(document.body).getPropertyValue("--bg-primary").trim(),
    );
  for (const mesh of renderNodes) {
    mesh.material.color.set(light ? "#3f7771" : "#c5e9e5");
    mesh.material.emissive.set(light ? "#3f7771" : "#c5e9e5");
  }
  const icon = document.createElement("i");
  icon.dataset.lucide = light ? "moon" : "sun";
  $("theme-toggle").replaceChildren(icon);
  icons();
}
$("theme-toggle").addEventListener("click", () => {
  document.body.classList.toggle("light");
  localStorage.setItem(
    "jarvis_theme",
    document.body.classList.contains("light") ? "light" : "dark",
  );
  applySceneTheme();
});
initScene();
applySceneTheme();
buildGraph();
pauseLabel();
updateClock();
refresh();
setInterval(() => {
  updateClock();
  if (!document.hidden) refresh();
}, 60000);
