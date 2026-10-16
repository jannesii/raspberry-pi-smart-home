// static/js/3d.js

// Initial load
console.log('📦 Dashboard script loaded');

// Wait for DOM to be ready
document.addEventListener('DOMContentLoaded', () => {
    console.log('📑 DOMContentLoaded fired');

    const video = document.getElementById('live');
    const imageEl = document.getElementById('image');
    const liveVideoEl = document.getElementById('live-video');

    // Decide what to show initially based on server-provided status
    const preferImage = (window.preferImage === true);

    let hls = null;
    if (!preferImage) {
      try {
        hls = new Hls({
          liveSyncDurationCount: 1,
          maxLiveSyncPlaybackRate: 2.0
        });
        hls.loadSource('/live/printer1/index.m3u8');
        hls.attachMedia(video);
        // Auto-reseek if we drift >3 s
        hls.on(Hls.Events.LEVEL_UPDATED, () => {
          const lag = (hls.liveSyncPosition || 0) - video.currentTime;
          if (lag > 3) video.currentTime = hls.liveSyncPosition;
        });
        // Ensure correct tiles visible if HLS is enabled
        liveVideoEl.classList.remove('hidden');
        imageEl.classList.add('hidden');
      } catch (e) {
        console.warn('⚠️ HLS init failed, falling back to image:', e);
        // Fallback to image
        liveVideoEl.classList.add('hidden');
        imageEl.classList.remove('hidden');
      }
    } else {
      // Prefer image: hide video
      liveVideoEl.classList.add('hidden');
      imageEl.classList.remove('hidden');
    }

    // Keep video in sync when tab becomes visible (only if HLS active)
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden && hls && hls.liveSyncPosition)
        video.currentTime = hls.liveSyncPosition;
    });

    socket.on('image', data => {
      // If server ever includes base64 image in payload, render it.
      try {
        if (data && data.image) {
          document.querySelector('.image-tile img').src = 'data:image/jpeg;base64,' + data.image;
          console.log('🖼️ Image tile updated from payload');
        } else {
          console.log('📷 Image event received without payload; no update performed');
        }
      } catch (e) {
        console.warn('⚠️ Failed to update image from payload:', e);
      }
    });

    // ─── Printer elements ─────────────────────────────────────────
    const bedTempEl      = document.getElementById('bedTemp');
    const nozzleTempEl   = document.getElementById('nozzleTemp');
    const nozzleTypeEl   = document.getElementById('nozzleType');
    const nozzleDiamEl   = document.getElementById('nozzleDiam');
    const printerStatEl  = document.getElementById('printerStatus');
    const gcodeStatEl    = document.getElementById('gcodeStatus');
    const printerStatTile= document.getElementById('printerStatusTile');
    //  ─ progress‑bar DOM elements ─
    const fileNameEl      = document.getElementById('fileName');
    const progPercentEl   = document.getElementById('progressPercent');
    const progBarEl       = document.getElementById('progressBar');
    const layerInfoEl     = document.getElementById('layerInfo');
    const remainingTimeEl = document.getElementById('remainingTime');
    const printSpeedEl    = document.getElementById('printSpeed');
    // ── Timelapse status ──
    const timelapseStatusEl = document.getElementById('timelapseStatus');

    // elements declared earlier

    function formatTime(minutes) {
      // use total whole minutes
      const totalMinutes = Math.floor(minutes);

      const h = Math.floor(totalMinutes / 60)
        .toString()
        .padStart(2, '0');
      const m = (totalMinutes % 60)
        .toString()
        .padStart(2, '0');

      return `${h}:${m}`;
    }

    socket.on('status', data => {
      console.log('ℹ️ Received status2v event:', data);

      if ('bed_temperature'    in data) bedTempEl.textContent    = data.bed_temperature.toFixed(1) + ' °C';
      if ('nozzle_temperature' in data) nozzleTempEl.textContent = data.nozzle_temperature.toFixed(1) + ' °C';
      if ('nozzle_type'        in data) nozzleTypeEl.textContent = data.nozzle_type;
      if ('nozzle_diameter'    in data) nozzleDiamEl.textContent = data.nozzle_diameter + ' mm';
      if ('status'             in data){
        printerStatEl.textContent = data.status;
        printerStatTile.dataset.state = data.status;   // colour via CSS
      }
      if ('gcode_status'       in data){
        gcodeStatEl.textContent  = data.gcode_status;
      }

      // ── progress‑bar update (guard against partially‑filled packets) ──
      if ('percentage' in data){
        progBarEl.style.width   = data.percentage + '%';
        progPercentEl.textContent = data.percentage + ' %';
      }

      if ('file_name' in data) fileNameEl.textContent = data.file_name;

      if ('current_layer' in data && 'total_layers' in data){
        layerInfoEl.textContent = `Layer ${data.current_layer} / ${data.total_layers}`;
      }

      if ('remaining_time' in data){
        remainingTimeEl.textContent = formatTime(data.remaining_time);
      }

      if ('print_speed' in data){
        printSpeedEl.textContent = data.print_speed + ' mm/s';
      }
      // ── Timelapse status ──
      if ('timelapse_status' in data) {
        // Boolean timelapse status from client (preferred live signal)
        const isActive = (data.timelapse_status === true) || (data.timelapse_status === 'True');
        // Toggle tiles
        if (isActive) {
          // Timelapse active -> prefer image
          liveVideoEl.classList.add('hidden');
          imageEl.classList.remove('hidden');
        } else {
          // Timelapse inactive -> try live video, else fallback to image
          if (!hls) {
            try {
              hls = new Hls({ liveSyncDurationCount: 1, maxLiveSyncPlaybackRate: 2.0 });
              hls.loadSource('/live/printer1/index.m3u8');
              hls.attachMedia(video);
            } catch (e) {
              console.warn('⚠️ HLS init on status failed, staying on image:', e);
            }
          }
          if (hls) {
            liveVideoEl.classList.remove('hidden');
            imageEl.classList.add('hidden');
          } else {
            liveVideoEl.classList.add('hidden');
            imageEl.classList.remove('hidden');
          }
        }
        timelapseStatusEl.innerHTML = isActive
          ? '<button class="control-btn-status start-btn">Active</button>'
          : '<button class="control-btn-status stop-btn">Inactive</button>';
      }
    });

    socket.on('temphum2v', data => {
      console.log('🌡️ Received temphum2v event, data:', data);
      document.getElementById('temperature').innerText = data.temperature + '°C';
      document.getElementById('humidity').innerText    = data.humidity + '%';
      console.log('🌡️ Temperature/Humidity updated:', data.temperature, data.humidity);
    });

    // ── printer control buttons ─
    document.querySelectorAll('.control-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        var body = {};
        const action = btn.dataset.action;
        body.action = action;  // unified action name
        if (action == 'run_gcode') {
          const gcodeInput = document.getElementById('gcodeInput').value.trim();
          body.gcode = gcodeInput;
        }
        console.log(`🔘 "${action}" button clicked`);
        socket.emit('printerAction', body);
      });
    });
    // ─── G‑code autocomplete ──────────────────────────────────────
    const gcodeInput  = document.getElementById('gcodeInput');
    const suggestBox  = document.getElementById('gcodeSuggest');
    let   gcodeCache  = [];

    // one fetch at startup
    fetch('/api/gcode')
      .then(r => r.json())
      .then(data => { gcodeCache = data.gcode_list || []; })
      .catch(err => console.error('⚠️ Could not load /api/gcode:', err));

    /* helpers ----------------------------------------------------- */
    function closeSuggest(){ suggestBox.style.display='none'; }
    function showSuggest(items){
      suggestBox.innerHTML = items
        .map(cmd => `<li class="autocomplete-item">${cmd}</li>`).join('');
      suggestBox.style.display = items.length ? 'block' : 'none';
    }

    /* replace the partial word before caret with full command */
    function insertCmd(cmd){
      const {selectionStart:pos, value:txt} = gcodeInput;
      const before = txt.slice(0,pos);
      const after  = txt.slice(pos);
      const chunks = before.split(/\n/);          // last line only
      chunks[chunks.length-1] = cmd;              // replace partial
      gcodeInput.value = chunks.join('\n') + after;
      gcodeInput.focus();
      closeSuggest();
    }

    /* main listener ---------------------------------------------- */
    gcodeInput.addEventListener('input', () => {
      const {selectionStart:pos, value:txt} = gcodeInput;
      const before = txt.slice(0,pos);
      const last   = before.split(/\n/).pop().trim().toUpperCase();

      if(!last){ closeSuggest(); return; }

      const hits = gcodeCache
                    .filter(c => c.toUpperCase().startsWith(last))
                    .slice(0,10);                // top‑10 only
      showSuggest(hits);
    });

    /* click‑to‑choose */
    suggestBox.addEventListener('click', e => {
      if(e.target.classList.contains('autocomplete-item')){
        insertCmd(e.target.textContent);
      }
    });

    /* basic up/down/enter navigation */
    gcodeInput.addEventListener('keydown', e => {
      const active = suggestBox.querySelector('.active');
      let next;
      if(e.key==='ArrowDown'){
        next = active ? active.nextSibling : suggestBox.firstChild;
      }else if(e.key==='ArrowUp'){
        next = active ? active.previousSibling : suggestBox.lastChild;
      }else if(e.key==='Enter' && active){
        e.preventDefault();
        insertCmd(active.textContent);
        return;
      }else{
        return; // let other keys through
      }
      if(active) active.classList.remove('active');
      if(next){ next.classList.add('active'); }
    });

    /* hide list when focus leaves */
    gcodeInput.addEventListener('blur', () => setTimeout(closeSuggest,150));



    // Tile click handlers
    document.getElementById('tempTile').addEventListener('click', () => {
      console.log('🎯 tempTile clicked');
      currentField = 'temperature';
      currentDate  = new Date();
      openModal();
      fetchAndRenderModal(currentField);
    });
    document.getElementById('humTile').addEventListener('click', () => {
      console.log('🎯 humTile clicked');
      currentField = 'humidity';
      currentDate  = new Date();
      openModal();
      fetchAndRenderModal(currentField);
    });

    // Prev/Next day
    prevBtn.addEventListener('click', () => {
      console.log('⬅️ prevDay clicked, before:', currentDate.toISOString());
      currentDate.setDate(currentDate.getDate() - 1);
      console.log('⬅️ new currentDate:', currentDate.toISOString());
      fetchAndRender(currentField);
    });
    nextBtn.addEventListener('click', () => {
      console.log('➡️ nextDay clicked, before:', currentDate.toISOString());
      currentDate.setDate(currentDate.getDate() + 1);
      console.log('➡️ new currentDate:', currentDate.toISOString());
      fetchAndRender(currentField);
    });


});
