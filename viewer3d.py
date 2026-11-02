"""
viewer3d.py
Visualizador 3D Avançado — Arcada Dentária Anatômica com Seleção de Faces
"""

VIEWER_3D = r"""
<div class="sl">🦷 Visualizador 3D — Arcada Anatômica (Padrão SUS)</div>

<div style="background:#0f172a;border:1.5px solid #1e3a8a;border-radius:16px;overflow:hidden;margin-bottom:18px;box-shadow:0 4px 12px rgba(15,23,42,0.24);">

  <div style="background:#111827;border-bottom:1.5px solid #1e3a8a;padding:10px 16px;
       display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;">
    <span style="font-size:12px;font-weight:700;color:#60a5fa;display:flex;align-items:center;gap:6px;">
      ⬡ Modelo Anatômico 3D Multifacetado
    </span>
    <div style="display:flex;gap:5px;flex-wrap:wrap;" id="v3d-toolbar">
      <button class="v3d-btn v3d-on" data-mode="solid"    onclick="v3dMode('solid',this)">■ Sólido</button>
      <button class="v3d-btn" data-mode="wireframe" onclick="v3dMode('wireframe',this)">⬡ Wireframe</button>
      <button class="v3d-btn" data-mode="xray"      onclick="v3dMode('xray',this)">◎ Raio-X</button>
      <button class="v3d-btn" onclick="v3dReset()">↺ Reset</button>
    </div>
    <div style="display:flex;gap:5px;" id="v3d-vistas">
      <button class="v3d-btn v3d-on" onclick="v3dVista('boca',this)">👄 Boca Completa</button>
      <button class="v3d-btn" onclick="v3dVista('superior',this)">⬆ Arcada Sup.</button>
      <button class="v3d-btn" onclick="v3dVista('inferior',this)">⬇ Arcada Inf.</button>
    </div>
  </div>

  <div id="v3d-wrap" style="position:relative;height:420px;background:radial-gradient(circle at center, #111827 0%, #020617 100%);cursor:grab;overflow:hidden;user-select:none;">
    <canvas id="v3d-canvas" style="position:absolute;inset:0;width:100%;height:100%;display:block;"></canvas>
    
    <div style="position:absolute;top:12px;left:14px;pointer-events:none;background:rgba(15,23,42,0.85);padding:8px 12px;border-radius:8px;backdrop-filter:blur(4px);border:1px solid rgba(96,165,250,0.35);">
      <div id="v3d-hud-rot"  style="font-size:10px;font-family:monospace;color:#93c5fd;line-height:1.6;">ROT 0° / 15°</div>
      <div id="v3d-hud-zoom" style="font-size:10px;font-family:monospace;color:#93c5fd;line-height:1.6;">ZOOM 1.0x</div>
      <div id="v3d-hud-selected" style="font-size:11px;font-family:monospace;color:#facc15;font-weight:bold;line-height:1.6;">SELEÇÃO: Nenhuma face</div>
    </div>
    
    <div style="position:absolute;bottom:12px;left:50%;transform:translateX(-50%);
         font-size:11px;color:#cbd5e1;pointer-events:none;white-space:nowrap;background:rgba(15,23,42,0.8);padding:4px 12px;border-radius:20px;border:1px solid rgba(96,165,250,0.25);">
      🖱️ Arraste para girar · Clique na face do dente para selecionar
    </div>
  </div>

  <div style="background:#f8fafc;border-top:1.5px solid #e2e8f0;
       display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;padding:12px 16px;">
    <div>
      <div style="font-size:9px;color:#64748b;font-weight:700;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;">↔ Gira Horizontal</div>
      <div style="display:flex;align-items:center;gap:6px;">
        <input type="range" id="v3d-rh" min="0" max="360" value="0" step="1" style="flex:1;" oninput="v3dOnRH(this.value)">
        <span id="v3d-rh-v" style="font-size:11px;font-family:monospace;min-width:30px;">0°</span>
      </div>
    </div>
    <div>
      <div style="font-size:9px;color:#64748b;font-weight:700;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;">↕ Gira Vertical</div>
      <div style="display:flex;align-items:center;gap:6px;">
        <input type="range" id="v3d-rv" min="-90" max="90" value="15" step="1" style="flex:1;" oninput="v3dOnRV(this.value)">
        <span id="v3d-rv-v" style="font-size:11px;font-family:monospace;min-width:30px;">15°</span>
      </div>
    </div>
    <div>
      <div style="font-size:9px;color:#64748b;font-weight:700;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;">🔍 Zoom</div>
      <div style="display:flex;align-items:center;gap:6px;">
        <input type="range" id="v3d-zm" min="0.5" max="2.5" value="1.0" step="0.05" style="flex:1;" oninput="v3dOnZoom(this.value)">
        <span id="v3d-zm-v" style="font-size:11px;font-family:monospace;min-width:32px;">1.0x</span>
      </div>
    </div>
  </div>
</div>

<style>
.sl{font-size:16px;font-weight:700;color:#60a5fa;margin:22px 0 10px;}
.v3d-btn{
  font-size:10px;font-weight:700;padding:6px 11px;border-radius:6px;
  border:1.5px solid #1e3a8a;background:#0f172a;color:#e2e8f0;
  cursor:pointer;transition:all .15s;font-family:inherit;}
.v3d-btn:hover{border-color:#60a5fa;color:#ffffff;}
.v3d-btn.v3d-on{background:#1e3a8a;color:#fff;border-color:#60a5fa;}
</style>

<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script>
(function(){
var sc, cam, ren, mouthGroup, rH=0, rV=15, zm=1.0, md='solid', drag=false, lx=0, ly=0;
var raycaster = new THREE.Raycaster();
var mouse = new THREE.Vector2();
var selectedMesh = null;

var matEsmalte = new THREE.MeshPhongMaterial({color: 0xf8fafc, specular: 0xffffff, shininess: 90});
var matGengiva = new THREE.MeshPhongMaterial({color: 0xe11d48, specular: 0xfda4af, shininess: 30});
var matSelected = new THREE.MeshPhongMaterial({color: 0xfacc15, specular: 0xffffff, shininess: 100, emissive: 0x854d0e});

function init(){
  var w = document.getElementById('v3d-wrap');
  var canvas = document.getElementById('v3d-canvas');
  var W = w.clientWidth || 700, H = w.clientHeight || 420;

  sc = new THREE.Scene();
  cam = new THREE.PerspectiveCamera(45, W / H, 0.1, 100);
  ren = new THREE.WebGLRenderer({canvas: canvas, antialias: true, alpha: true});
  ren.setSize(W, H);
  ren.setPixelRatio(Math.min(window.devicePixelRatio, 2));

  sc.add(new THREE.AmbientLight(0xffffff, 0.7));
  var mainLight = new THREE.DirectionalLight(0xffffff, 1.0); mainLight.position.set(5, 8, 5); sc.add(mainLight);
  var fillLight = new THREE.DirectionalLight(0x90b4ce, 0.5); fillLight.position.set(-5, -2, -5); sc.add(fillLight);

  buildFloatingMouth();
  setupCtrl(w);
  setupResize(w);
  loop();
}

function createAnatomicalTooth(denteNum) {
  var toothGroup = new THREE.Group();
  toothGroup.name = "Dente_" + denteNum;

  var w = 0.3, h = 0.5, d = 0.3;
  var facesGeo = [
    { name: 'Oclusal',    geo: new THREE.BoxGeometry(w, 0.05, d), pos: [0, h/2, 0] },
    { name: 'Vestibular', geo: new THREE.BoxGeometry(w, h, 0.05), pos: [0, 0, d/2] },
    { name: 'Lingual',    geo: new THREE.BoxGeometry(w, h, 0.05), pos: [0, 0, -d/2] },
    { name: 'Mesial',     geo: new THREE.BoxGeometry(0.05, h, d), pos: [-w/2, 0, 0] },
    { name: 'Distal',     geo: new THREE.BoxGeometry(0.05, h, d), pos: [w/2, 0, 0] }
  ];

  facesGeo.forEach(function(f) {
    var mesh = new THREE.Mesh(f.geo, matEsmalte.clone());
    mesh.position.set(f.pos[0], f.pos[1], f.pos[2]);
    mesh.userData = { dente: denteNum, face: f.name };
    toothGroup.add(mesh);
  });

  return toothGroup;
}

function buildFloatingMouth() {
  if (mouthGroup) sc.remove(mouthGroup);
  mouthGroup = new THREE.Group();

  var arcadaSuperior = buildArch(true);
  var arcadaInferior = buildArch(false);

  arcadaSuperior.position.y = 0.45;
  arcadaInferior.position.y = -0.45;

  mouthGroup.add(arcadaSuperior);
  mouthGroup.add(arcadaInferior);

  sc.add(mouthGroup);
  applyMode();
}

function buildArch(isUpper) {
  var arch = new THREE.Group();
  var dentes = isUpper ? [18,17,16,15,14,13,12,11,21,22,23,24,25,26,27,28] : [48,47,46,45,44,43,42,41,31,32,33,34,35,36,37,38];
  var numTeeth = dentes.length;
  var a = 1.8, b = 2.2;

  var gumGeo = new THREE.TorusGeometry(a, 0.38, 12, 32, Math.PI);
  var gum = new THREE.Mesh(gumGeo, matGengiva.clone());
  gum.rotation.x = isUpper ? Math.PI / 2 : -Math.PI / 2;
  arch.add(gum);

  for (var i = 0; i < numTeeth; i++) {
    var t = (i / (numTeeth - 1)) * Math.PI - (Math.PI / 2);
    var x = a * Math.sin(t);
    var z = b * Math.cos(t) - 1.2;

    var tooth = createAnatomicalTooth(dentes[i]);
    tooth.position.set(x, isUpper ? -0.2 : 0.2, z);
    tooth.rotation.y = -t;
    arch.add(tooth);
  }

  return arch;
}

function applyMode(){
  if(!mouthGroup) return;
  mouthGroup.traverse(function(o){
    if(!o.isMesh) return;
    if(o === selectedMesh) return;
    if(md === 'solid'){
      o.material.wireframe = false; o.material.transparent = false; o.material.opacity = 1;
    } else if(md === 'wireframe'){
      o.material.wireframe = true; o.material.transparent = false; o.material.opacity = 1;
    } else if(md === 'xray'){
      o.material.wireframe = false; o.material.transparent = true; o.material.opacity = 0.4;
    }
    o.material.needsUpdate = true;
  });
}

function loop(){
  requestAnimationFrame(loop);
  if(!drag && mouthGroup) mouthGroup.rotation.y += 0.002;
  
  var r = 7.5 / zm, rHr = rH * Math.PI / 180, rVr = rV * Math.PI / 180;
  cam.position.x = r * Math.sin(rHr) * Math.cos(rVr);
  cam.position.y = r * Math.sin(rVr);
  cam.position.z = r * Math.cos(rHr) * Math.cos(rVr);
  cam.lookAt(0, 0, 0);
  ren.render(sc, cam);
}

function setupCtrl(w){
  w.addEventListener('mousedown', function(e){ drag = true; lx = e.clientX; ly = e.clientY; w.style.cursor = 'grabbing'; });
  window.addEventListener('mouseup', function(){ drag = false; if(w) w.style.cursor = 'grab'; });
  w.addEventListener('mousemove', function(e){
    if(!drag) return;
    rH += (e.clientX - lx) * 0.5;
    rV = Math.max(-85, Math.min(85, rV - (e.clientY - ly) * 0.5));
    lx = e.clientX; ly = e.clientY;
    if(mouthGroup) mouthGroup.rotation.y = 0;
    updateSlidersUI();
  });

  w.addEventListener('click', function(e){
    var rect = ren.domElement.getBoundingClientRect();
    mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;

    raycaster.setFromCamera(mouse, cam);
    var intersects = raycaster.intersectObjects(mouthGroup.children, true);

    if (intersects.length > 0) {
      var hit = intersects[0].object;
      if (hit.userData && hit.userData.dente) {
        if (selectedMesh) selectedMesh.material = matEsmalte.clone();
        selectedMesh = hit;
        selectedMesh.material = matSelected;

        document.getElementById('v3d-hud-selected').textContent = 'SELEÇÃO: Dente ' + hit.userData.dente + ' (' + hit.userData.face + ')';
        
        // Preenche automaticamente o formulário do Odontograma se existir na tela
        var inputDente = document.getElementById('field-dente');
        var inputFace = document.getElementById('field-face');
        if(inputDente) inputDente.value = hit.userData.dente;
        if(inputFace) inputFace.value = hit.userData.face;
      }
    }
  });

  w.addEventListener('wheel', function(e){
    e.preventDefault();
    zm = Math.max(0.5, Math.min(2.5, zm - e.deltaY * 0.001));
    document.getElementById('v3d-zm').value = zm;
    document.getElementById('v3d-zm-v').textContent = zm.toFixed(1) + 'x';
    document.getElementById('v3d-hud-zoom').textContent = 'ZOOM ' + zm.toFixed(1) + 'x';
  }, {passive: false});
}

function updateSlidersUI(){
  var normH = Math.round(((rH % 360) + 360) % 360);
  var normV = Math.round(rV);
  document.getElementById('v3d-rh').value = normH;
  document.getElementById('v3d-rh-v').textContent = normH + '°';
  document.getElementById('v3d-rv').value = normV;
  document.getElementById('v3d-rv-v').textContent = normV + '°';
  document.getElementById('v3d-hud-rot').textContent = 'ROT ' + normH + '° / ' + normV + '°';
}

function setupResize(w){
  if(window.ResizeObserver){
    new ResizeObserver(function(entries){
      for(var entry of entries){
        var W = entry.contentRect.width;
        var H = entry.contentRect.height || 420;
        if(cam && ren){
          cam.aspect = W / H;
          cam.updateProjectionMatrix();
          ren.setSize(W, H);
        }
      }
    }).observe(w);
  }
}

window.v3dMode = function(m, btn){
  md = m; applyMode();
  document.querySelectorAll('#v3d-toolbar .v3d-btn').forEach(function(b){ b.classList.remove('v3d-on'); });
  btn.classList.add('v3d-on');
};

window.v3dVista = function(vista, btn){
  document.querySelectorAll('#v3d-vistas .v3d-btn').forEach(function(b){ b.classList.remove('v3d-on'); });
  btn.classList.add('v3d-on');

  if(vista === 'superior'){ rH = 0; rV = 80; }
  else if(vista === 'inferior'){ rH = 0; rV = -80; }
  else { rH = 0; rV = 15; }
  updateSlidersUI();
};

window.v3dReset = function(){
  if (selectedMesh) selectedMesh.material = matEsmalte.clone();
  selectedMesh = null;
  document.getElementById('v3d-hud-selected').textContent = 'SELEÇÃO: Nenhuma face';
  rH = 0; rV = 15; zm = 1.0;
  document.getElementById('v3d-zm').value = 1.0;
  document.getElementById('v3d-zm-v').textContent = '1.0x';
  document.getElementById('v3d-hud-zoom').textContent = 'ZOOM 1.0x';
  updateSlidersUI();
};

window.v3dOnRH = function(v){ rH = parseFloat(v); updateSlidersUI(); if(mouthGroup) mouthGroup.rotation.y = 0; };
window.v3dOnRV = function(v){ rV = parseFloat(v); updateSlidersUI(); };
window.v3dOnZoom = function(v){ zm = parseFloat(v); document.getElementById('v3d-zm-v').textContent = zm.toFixed(1) + 'x'; document.getElementById('v3d-hud-zoom').textContent = 'ZOOM ' + zm.toFixed(1) + 'x'; };

if(document.readyState === 'loading'){ document.addEventListener('DOMContentLoaded', init); } else { init(); }
})();
</script>
"""