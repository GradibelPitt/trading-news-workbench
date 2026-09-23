import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

function normalizeTime(time) {
  if (typeof time === 'string' || typeof time === 'number') return String(time);
  if (time && typeof time === 'object') {
    const y = time.year ?? time.y;
    const m = time.month ?? time.m;
    const d = time.day ?? time.d;
    if (y && m && d) return `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
  }
  return '';
}

class Stock3DChart {
  constructor(container, payload, priceIndicators) {
    this.container = container;
    this.payload = payload;
    this.priceIndicators = priceIndicators || [];
    this.animFrameId = null;
    this.onResize = this.resize.bind(this);
    this.init();
  }

  init() {
    const candles = this.payload.candles || [];
    if (candles.length === 0) {
      this.container.innerHTML = '<div style="color:var(--muted);padding:40px;text-align:center;">No candle data available for 3D visualization.</div>';
      return;
    }

    // Map other metrics by time
    const volumeMap = new Map((this.payload.volume || []).map(v => [normalizeTime(v.time), v.value]));
    const sma20Map = new Map((this.payload.sma20 || []).map(s => [normalizeTime(s.time), s.value]));
    const sma50Map = new Map((this.payload.sma50 || []).map(s => [normalizeTime(s.time), s.value]));
    const eventMarkersMap = new Map();
    (this.payload.eventMarkers || []).forEach(m => {
      const t = normalizeTime(m.time);
      if (!eventMarkersMap.has(t)) {
        eventMarkersMap.set(t, []);
      }
      eventMarkersMap.get(t).push(m);
    });

    const data = candles.map((c, i) => {
      const t = normalizeTime(c.time);
      return {
        i,
        time: t,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
        volume: volumeMap.get(t) || 0,
        ma20: sma20Map.get(t) || null,
        ma50: sma50Map.get(t) || null,
        event: eventMarkersMap.get(t) || null
      };
    });

    // 3D parameters setup
    const lowPrices = data.map(d => d.low).filter(v => v !== null && v !== undefined);
    const highPrices = data.map(d => d.high).filter(v => v !== null && v !== undefined);
    const volumes = data.map(d => d.volume).filter(v => v !== null && v !== undefined);

    const minP = Math.min(...lowPrices);
    const maxP = Math.max(...highPrices);
    const maxV = Math.max(...volumes);

    const priceDiff = maxP - minP;
    const scaleY = priceDiff > 0 ? 56 / priceDiff : 1;
    const maxVLog = Math.log1p(maxV) || 1;
    const xStep = 0.78;
    const x0 = -data.length * xStep / 2;
    this.dataLength = data.length;

    const y = (p) => (p - minP) * scaleY - 28;
    const x = (i) => x0 + i * xStep;
    this.y = y;
    this.x = x;
    this.timeToIdxMap = new Map();
    data.forEach(d => {
      this.timeToIdxMap.set(d.time, d.i);
    });

    // Three.js Core Setup
    this.scene = new THREE.Scene();
    this.scene.fog = new THREE.Fog(0x070d18, 65, 155);

    const w = this.container.clientWidth || 800;
    const h = this.container.clientHeight || 500;
    this.camera = new THREE.PerspectiveCamera(45, w / h, 0.1, 1000);
    this.camera.position.set(58, 44, 72);

    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setSize(w, h);
    this.renderer.shadowMap.enabled = true;
    
    // Clear container and append renderer element
    this.container.innerHTML = '';
    this.container.appendChild(this.renderer.domElement);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.target.set(0, 0, 0);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.06;
    this.controls.minDistance = 25;
    this.controls.maxDistance = 150;
    this.controls.maxPolarAngle = Math.PI * 0.49;
    this.controls.update();

    // Lighting
    this.scene.add(new THREE.AmbientLight(0x9db8ff, 0.65));
    const key = new THREE.DirectionalLight(0xffffff, 1.7);
    key.position.set(22, 60, 35);
    key.castShadow = true;
    this.scene.add(key);

    const rim = new THREE.PointLight(0x58fff2, 2.2, 100);
    rim.position.set(-25, 22, 35);
    this.scene.add(rim);

    const hot = new THREE.PointLight(0xff526d, 1.4, 90);
    hot.position.set(45, 18, -32);
    this.scene.add(hot);

    // Groups
    this.root = new THREE.Group();
    this.scene.add(this.root);

    this.candleGroup = new THREE.Group();
    this.root.add(this.candleGroup);

    this.volumeGroup = new THREE.Group();
    this.root.add(this.volumeGroup);

    this.maGroup = new THREE.Group();
    this.root.add(this.maGroup);

    this.dynamicIndicatorsGroup = new THREE.Group();
    this.root.add(this.dynamicIndicatorsGroup);

    this.eventGroup = new THREE.Group();
    this.root.add(this.eventGroup);

    this.fusedGroup = new THREE.Group();
    this.root.add(this.fusedGroup);

    this.tradePlanGroup = new THREE.Group();
    this.root.add(this.tradePlanGroup);

    // Ground Grid & Floor
    const grid = new THREE.GridHelper(150, 75, 0x355074, 0x1b2b45);
    grid.position.y = -30;
    this.root.add(grid);

    const floor = new THREE.Mesh(
      new THREE.PlaneGeometry(150, 70),
      new THREE.MeshStandardMaterial({
        color: 0x08111f,
        transparent: true,
        opacity: 0.52,
        metalness: 0.2,
        roughness: 0.72
      })
    );
    floor.rotation.x = -Math.PI / 2;
    floor.position.y = -30.2;
    floor.receiveShadow = true;
    this.root.add(floor);

    // Axis Lines
    const addAxis = () => {
      const matX = new THREE.LineBasicMaterial({ color: 0x58fff2, transparent: true, opacity: 0.9 });
      const matY = new THREE.LineBasicMaterial({ color: 0x20e188, transparent: true, opacity: 0.9 });
      const matZ = new THREE.LineBasicMaterial({ color: 0x6d8fff, transparent: true, opacity: 0.9 });
      const lines = [
        [[-68, -30, -22], [68, -30, -22], matX],
        [[-68, -30, -22], [-68, 36, -22], matY],
        [[-68, -30, -22], [-68, -30, 28], matZ]
      ];
      for (const [a, b, m] of lines) {
        const g = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(...a), new THREE.Vector3(...b)]);
        this.root.add(new THREE.Line(g, m));
      }
    };
    addAxis();

    // Materials
    const upMat = new THREE.MeshStandardMaterial({ color: 0x22c55e, emissive: 0x083f29, metalness: 0.25, roughness: 0.4 });
    const downMat = new THREE.MeshStandardMaterial({ color: 0xef4444, emissive: 0x4a0914, metalness: 0.25, roughness: 0.42 });
    const wickUp = new THREE.LineBasicMaterial({ color: 0x22c55e, transparent: true, opacity: 0.92 });
    const wickDown = new THREE.LineBasicMaterial({ color: 0xef4444, transparent: true, opacity: 0.92 });
    const volumeMat = new THREE.MeshStandardMaterial({ color: 0x2b7cff, transparent: true, opacity: 0.48, emissive: 0x0b1d46, metalness: 0.1, roughness: 0.6 });

    // Render Candles, Volumes, and Events
    data.forEach(d => {
      const isUp = d.close >= d.open;
      const bodyH = Math.max(Math.abs(y(d.close) - y(d.open)), 0.35);
      
      // Box body
      const body = new THREE.Mesh(new THREE.BoxGeometry(0.45, bodyH, 0.52), isUp ? upMat : downMat);
      body.position.set(x(d.i), (y(d.close) + y(d.open)) / 2, 0);
      body.castShadow = true;
      this.candleGroup.add(body);

      // Shadow wick
      const wickGeo = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(x(d.i), y(d.low), 0),
        new THREE.Vector3(x(d.i), y(d.high), 0)
      ]);
      this.candleGroup.add(new THREE.Line(wickGeo, isUp ? wickUp : wickDown));

      // Volume pillar
      const vh = 1.2 + (Math.log1p(d.volume) / maxVLog) * 19;
      const vb = new THREE.Mesh(new THREE.BoxGeometry(0.48, vh, 0.7), volumeMat);
      vb.position.set(x(d.i), -30 + vh / 2, -13.5);
      this.volumeGroup.add(vb);

      // Event marker
      if (d.event && d.event.length > 0) {
        const geom = new THREE.SphereGeometry(0.85, 18, 18);
        const types = new Set(d.event.map(m => m.eventType));
        
        let eventColor = 0xffc857; // orange default
        let emissiveColor = 0x5a3d00;
        
        if (types.has('earnings') && types.has('dividend')) {
          eventColor = 0xa78bfa; // purple for combined
          emissiveColor = 0x3b0764;
        } else if (types.has('earnings')) {
          eventColor = 0x3b82ff; // blue
          emissiveColor = 0x0c2a70;
        }
        
        const mat = new THREE.MeshStandardMaterial({
          color: eventColor,
          emissive: emissiveColor,
          metalness: 0.25,
          roughness: 0.3
        });
        const sph = new THREE.Mesh(geom, mat);
        sph.position.set(x(d.i), -27.2, 10);
        this.eventGroup.add(sph);
      }
    });

    // Draw SMA lines
    const addMALine = (values, color, zoff) => {
      const pts = [];
      values.forEach((v, i) => {
        if (v !== null && v !== undefined) {
          pts.push(new THREE.Vector3(x(i), y(v) + 0.28, zoff));
        }
      });
      if (pts.length > 1) {
        const geo = new THREE.BufferGeometry().setFromPoints(pts);
        const mat = new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.96, linewidth: 2 });
        this.maGroup.add(new THREE.Line(geo, mat));
      }
    };

    const ma20Vals = data.map(d => d.ma20);
    const ma50Vals = data.map(d => d.ma50);
    addMALine(ma20Vals, 0x58fff2, 0.95);
    addMALine(ma50Vals, 0xffc857, 1.6);

    this.renderPriceIndicators();
    this.createMaximizeButton();
    this.createLegendOverlay();

    window.addEventListener('resize', this.onResize);
    
    // Animation Loop
    const animate = () => {
      this.controls.update();
      this.renderer.render(this.scene, this.camera);
      this.animFrameId = requestAnimationFrame(animate);
    };
    animate();
  }

  updatePriceIndicators(priceIndicators) {
    this.priceIndicators = priceIndicators || [];
    this.renderPriceIndicators();
    this.updateLegend();
  }

  clearGroup(group) {
    if (!group) return;
    while (group.children.length > 0) {
      const obj = group.children[0];
      group.remove(obj);
      if (obj.geometry) obj.geometry.dispose();
      if (obj.material) {
        if (Array.isArray(obj.material)) {
          obj.material.forEach(m => m.dispose());
        } else {
          obj.material.dispose();
        }
      }
    }
  }

  updateFusedIndicators(paneIndicators) {
    // Volume is rendered separately via toggleVolume in the main chart.
    // It must not be duplicated in fusedIndicators.
    this.fusedIndicators = (paneIndicators || []).filter(item => item && item.key !== 'volume');
    this.renderFusedIndicators();
    this.updateLegend();
  }

  renderFusedIndicators() {
    if (!this.fusedGroup) return;

    // Clean up previous meshes/lines/materials/geometries strictly (Condition 5)
    this.clearGroup(this.fusedGroup);

    if (!this.fusedIndicators || this.fusedIndicators.length === 0) return;

    // Materials cache to avoid duplicate instantiation
    const upHistogramMat = new THREE.MeshStandardMaterial({ color: 0x22c55e, emissive: 0x083f29, metalness: 0.2, roughness: 0.4 });
    const downHistogramMat = new THREE.MeshStandardMaterial({ color: 0xef4444, emissive: 0x4a0914, metalness: 0.2, roughness: 0.4 });

    let groupIdx = 0;
    const baseZ = -22;
    const zSpacing = -6;

    this.fusedIndicators.forEach((item) => {
      // Find min and max for this indicator item group (Condition 3)
      const allVals = [];
      (item.series || []).forEach((seriesDef) => {
        (seriesDef.data || []).forEach((d) => {
          if (d.value !== null && d.value !== undefined) {
            allVals.push(d.value);
          }
        });
      });

      if (allVals.length === 0) return;

      let minVal, maxVal;
      const keyLower = String(item.key || '').toLowerCase();
      // KDJ/RSI use 0-100 range strictly
      if (keyLower.includes('rsi') || keyLower.includes('kdj')) {
        minVal = 0;
        maxVal = 100;
      } else {
        minVal = Math.min(...allVals);
        maxVal = Math.max(...allVals);
      }

      const valDiff = maxVal - minVal;
      const scaleY = valDiff > 0 ? 30 / valDiff : 1;
      const yFused = (v) => (v - minVal) * scaleY - 20;

      let yZero = -20;
      if (minVal <= 0 && maxVal >= 0) {
        yZero = (0 - minVal) * scaleY - 20;
      } else if (maxVal < 0) {
        yZero = 10;
      }

      // Group Z coordinate
      const groupZ = baseZ + groupIdx * zSpacing;
      groupIdx++;

      let seriesIdxInGroup = 0;
      const internalZOffsetSpacing = 0.12;

      (item.series || []).forEach((seriesDef) => {
        if (!Array.isArray(seriesDef.data) || seriesDef.data.length === 0) return;

        // Space series within the same group to prevent Z-fighting (Condition 3)
        const zVal = groupZ + seriesIdxInGroup * internalZOffsetSpacing;
        seriesIdxInGroup++;

        if (seriesDef.type === 'histogram') {
          // Draw as 3D boxes
          seriesDef.data.forEach((d) => {
            const t = normalizeTime(d.time);
            const idx = this.timeToIdxMap.get(t);
            if (idx !== undefined && d.value !== null && d.value !== undefined) {
              const valY = yFused(d.value);
              const height = Math.max(Math.abs(valY - yZero), 0.25);
              const center = (valY + yZero) / 2;
              
              const geom = new THREE.BoxGeometry(0.45, height, 0.5);
              const mat = d.value >= 0 ? upHistogramMat : downHistogramMat;
              const box = new THREE.Mesh(geom, mat);
              box.position.set(this.x(idx), center, zVal);
              this.fusedGroup.add(box);
            }
          });
        } else {
          // Draw as 3D Line
          const pts = [];
          seriesDef.data.forEach((d) => {
            const t = normalizeTime(d.time);
            const idx = this.timeToIdxMap.get(t);
            if (idx !== undefined && d.value !== null && d.value !== undefined) {
              pts.push(new THREE.Vector3(this.x(idx), yFused(d.value), zVal));
            }
          });

          if (pts.length > 1) {
            const geo = new THREE.BufferGeometry().setFromPoints(pts);
            let colorVal = 0x38bdf8;
            if (seriesDef.color) {
              try {
                colorVal = new THREE.Color(seriesDef.color);
              } catch (e) {
                // fallback
              }
            }
            const mat = new THREE.LineBasicMaterial({
              color: colorVal,
              transparent: true,
              opacity: 0.92,
              linewidth: 2
            });
            const line = new THREE.Line(geo, mat);
            this.fusedGroup.add(line);
          }
        }
      });
    });
  }

  renderPriceIndicators() {
    if (!this.dynamicIndicatorsGroup) return;

    // Clear existing children
    while (this.dynamicIndicatorsGroup.children.length > 0) {
      const obj = this.dynamicIndicatorsGroup.children[0];
      this.dynamicIndicatorsGroup.remove(obj);
      if (obj.geometry) obj.geometry.dispose();
      if (obj.material) {
        if (Array.isArray(obj.material)) {
          obj.material.forEach(m => m.dispose());
        } else {
          obj.material.dispose();
        }
      }
    }

    if (!this.priceIndicators || this.priceIndicators.length === 0) return;

    let seriesIdx = 0;
    const baseZ = 2.2;
    const zSpacing = 0.45;

    this.priceIndicators.forEach((item) => {
      (item.series || []).forEach((seriesDef) => {
        if (!Array.isArray(seriesDef.data) || seriesDef.data.length === 0) return;

        const pts = [];
        const zVal = baseZ + seriesIdx * zSpacing;
        seriesIdx++;

        seriesDef.data.forEach((d) => {
          const t = normalizeTime(d.time);
          const idx = this.timeToIdxMap.get(t);
          if (idx !== undefined && d.value !== null && d.value !== undefined) {
            pts.push(new THREE.Vector3(this.x(idx), this.y(d.value) + 0.28, zVal));
          }
        });

        if (pts.length > 1) {
          const geo = new THREE.BufferGeometry().setFromPoints(pts);
          let colorVal = 0x38bdf8;
          if (seriesDef.color) {
            try {
              colorVal = new THREE.Color(seriesDef.color);
            } catch (e) {
              // fallback
            }
          }
          const mat = new THREE.LineBasicMaterial({
            color: colorVal,
            transparent: true,
            opacity: 0.92,
            linewidth: 2
          });
          const line = new THREE.Line(geo, mat);
          this.dynamicIndicatorsGroup.add(line);
        }
      });
    });
  }

  updateTradePlan(plan) {
    this.tradePlan = plan || null;
    if (!this.tradePlanGroup || !this.x || !this.y) return;
    this.clearGroup(this.tradePlanGroup);
    const scenario = plan?.preferredSide ? plan[plan.preferredSide] : null;
    if (!scenario || !scenario.entryZone) {
      this.updateLegend();
      return;
    }
    const xStart = this.x(0) - 0.5;
    const xEnd = this.x(Math.max(0, (this.dataLength || 1) - 1)) + 0.5;
    const width = Math.max(1, xEnd - xStart);
    const entryLow = Number(scenario.entryZone.low);
    const entryHigh = Number(scenario.entryZone.high);
    if (Number.isFinite(entryLow) && Number.isFinite(entryHigh)) {
      const height = Math.max(0.2, Math.abs(this.y(entryHigh) - this.y(entryLow)));
      const plane = new THREE.Mesh(
        new THREE.PlaneGeometry(width, height),
        new THREE.MeshBasicMaterial({ color: 0x2dd4bf, transparent: true, opacity: 0.18, side: THREE.DoubleSide, depthWrite: false })
      );
      plane.position.set((xStart + xEnd) / 2, (this.y(entryLow) + this.y(entryHigh)) / 2, 4.0);
      this.tradePlanGroup.add(plane);
    }
    const addLevel = (price, color, z) => {
      const value = Number(price);
      if (!Number.isFinite(value)) return;
      const geometry = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(xStart, this.y(value), z),
        new THREE.Vector3(xEnd, this.y(value), z),
      ]);
      this.tradePlanGroup.add(new THREE.Line(geometry, new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.96 })));
    };
    addLevel(scenario.stop, 0xef4444, 4.2);
    addLevel(scenario.target1, 0xf59e0b, 4.4);
    addLevel(scenario.target2, 0x60a5fa, 4.6);
    addLevel(plan.levels?.vwap, 0xa78bfa, 4.8);
    addLevel(plan.levels?.anchoredVwap, 0xe879f9, 5.0);
    this.updateLegend();
  }


  toggleVolume(visible) {
    if (this.volumeGroup) this.volumeGroup.visible = visible;
  }

  toggleMA(visible) {
    if (this.maGroup) this.maGroup.visible = visible;
    this.updateLegend();
  }

  toggleEvents(visible) {
    if (this.eventGroup) this.eventGroup.visible = visible;
  }

  createLegendOverlay() {
    const legend = document.createElement('div');
    legend.className = 'chart-3d-legend';
    
    legend.style.position = 'absolute';
    legend.style.top = '15px';
    legend.style.left = '15px';
    legend.style.zIndex = '100';
    legend.style.background = 'rgba(15, 23, 42, 0.6)';
    legend.style.border = '1px solid rgba(255, 255, 255, 0.1)';
    legend.style.borderRadius = '10px';
    legend.style.color = '#e2e8f0';
    legend.style.padding = '10px 14px';
    legend.style.fontFamily = 'Inter, sans-serif';
    legend.style.fontSize = '12px';
    legend.style.lineHeight = '1.5';
    legend.style.pointerEvents = 'none';
    legend.style.backdropFilter = 'blur(6px)';
    legend.style.display = 'flex';
    legend.style.flexDirection = 'column';
    legend.style.gap = '4px';

    this.container.appendChild(legend);
    this.legendEl = legend;
    this.updateLegend();
  }

  updateLegend() {
    if (!this.legendEl) return;
    
    const candles = this.payload.candles || [];
    if (candles.length === 0) return;
    
    const latestCandle = candles[candles.length - 1];
    const formatVol = (val) => {
      if (!val) return '0';
      const absVal = Math.abs(val);
      let str = absVal.toFixed(0);
      if (absVal >= 1e6) str = (absVal / 1e6).toFixed(2) + 'M';
      else if (absVal >= 1e3) str = (absVal / 1e3).toFixed(1) + 'K';
      return val < 0 ? '-' + str : str;
    };
    const formatPrice = (val) => val !== null && val !== undefined ? Number(val).toFixed(val > 100 ? 2 : 3) : 'N/A';

    let html = `
      <div style="font-weight: 700; color: #f8fafc; font-size: 13px; margin-bottom: 2px;">
        ${this.payload.symbol || 'Stock'} (${this.payload.range || '1Y'} - ${this.payload.interval || '1D'})
      </div>
      <div style="display: flex; gap: 8px; flex-wrap: wrap; color: #94a3b8;">
        <span>O: <strong style="color:#cbd5e1">${formatPrice(latestCandle.open)}</strong></span>
        <span>H: <strong style="color:#22c55e">${formatPrice(latestCandle.high)}</strong></span>
        <span>L: <strong style="color:#ef4444">${formatPrice(latestCandle.low)}</strong></span>
        <span>C: <strong style="color:${latestCandle.close >= latestCandle.open ? '#22c55e' : '#ef4444'}">${formatPrice(latestCandle.close)}</strong></span>
        <span>V: <strong style="color:#cbd5e1">${formatVol(latestCandle.close >= latestCandle.open ? latestCandle.volume : -latestCandle.volume)}</strong></span>
      </div>
    `;

    const maVisible = this.maGroup ? this.maGroup.visible : true;
    if (maVisible) {
      const sma20 = this.payload.sma20 || [];
      const sma50 = this.payload.sma50 || [];
      const lastSma20 = sma20.length > 0 ? sma20[sma20.length - 1].value : null;
      const lastSma50 = sma50.length > 0 ? sma50[sma50.length - 1].value : null;
      
      if (lastSma20 !== null || lastSma50 !== null) {
        html += `
          <div style="display: flex; gap: 10px; margin-top: 2px;">
            ${lastSma20 !== null ? `<span style="color: #58fff2">SMA20: <strong>${formatPrice(lastSma20)}</strong></span>` : ''}
            ${lastSma50 !== null ? `<span style="color: #ffc857">SMA50: <strong>${formatPrice(lastSma50)}</strong></span>` : ''}
          </div>
        `;
      }
    }

    if (this.priceIndicators && this.priceIndicators.length > 0) {
      let indicatorHtml = '';
      this.priceIndicators.forEach((item) => {
        (item.series || []).forEach((seriesDef) => {
          if (Array.isArray(seriesDef.data) && seriesDef.data.length > 0) {
            const lastPoint = seriesDef.data[seriesDef.data.length - 1];
            if (lastPoint && lastPoint.value !== null && lastPoint.value !== undefined) {
              const name = seriesDef.name || item.name || 'Indicator';
              const color = seriesDef.color || '#38bdf8';
              indicatorHtml += `<span style="color: ${color}">${name}: <strong>${formatPrice(lastPoint.value)}</strong></span> `;
            }
          }
        });
      });
      if (indicatorHtml) {
        html += `<div style="display: flex; gap: 10px; flex-wrap: wrap; margin-top: 2px;">${indicatorHtml}</div>`;
      }
    }

    if (this.fusedIndicators && this.fusedIndicators.length > 0) {
      let fusedHtml = '';
      this.fusedIndicators.forEach((item) => {
        const seriesOutputs = [];
        (item.series || []).forEach((seriesDef) => {
          if (Array.isArray(seriesDef.data) && seriesDef.data.length > 0) {
            const lastPoint = seriesDef.data[seriesDef.data.length - 1];
            if (lastPoint && lastPoint.value !== null && lastPoint.value !== undefined) {
              const name = seriesDef.name || 'Value';
              const color = seriesDef.color || '#38bdf8';
              seriesOutputs.push(`<span style="color: ${color}">${name} <strong>${formatPrice(lastPoint.value)}</strong></span>`);
            }
          }
        });
        if (seriesOutputs.length > 0) {
          const lang = window.currentLang || 'zh';
          const itemLabel = lang === 'zh' ? (item.nameZh || item.nameEn || item.key) : (item.nameEn || item.nameZh || item.key);
          fusedHtml += `<div style="margin-top: 2px;"><span style="font-weight:700;color:#cbd5e1;">${itemLabel}:</span> ${seriesOutputs.join(' / ')}</div>`;
        }
      });
      if (fusedHtml) {
        html += `<div style="display:flex; flex-direction:column; gap:2px; margin-top:4px; border-top:1px solid rgba(255,255,255,0.08); padding-top:4px;">${fusedHtml}</div>`;
      }
    }

    const tradeScenario = this.tradePlan?.preferredSide ? this.tradePlan[this.tradePlan.preferredSide] : null;
    if (tradeScenario?.entryZone) {
      html += `
        <div style="display:flex;gap:9px;flex-wrap:wrap;margin-top:4px;border-top:1px solid rgba(255,255,255,.08);padding-top:4px;">
          <span style="color:#2dd4bf">ENTRY <strong>${formatPrice(tradeScenario.entryZone.low)}–${formatPrice(tradeScenario.entryZone.high)}</strong></span>
          <span style="color:#ef4444">STOP <strong>${formatPrice(tradeScenario.stop)}</strong></span>
          <span style="color:#f59e0b">T1 <strong>${formatPrice(tradeScenario.target1)}</strong></span>
          <span style="color:#60a5fa">T2 <strong>${formatPrice(tradeScenario.target2)}</strong></span>
        </div>`;
    }

    this.legendEl.innerHTML = html;
  }

  resetView() {
    if (this.camera && this.controls) {
      this.camera.position.set(58, 44, 72);
      this.controls.target.set(0, 0, 0);
      this.controls.update();
    }
  }

  resize() {
    if (!this.container || !this.renderer || !this.camera) return;
    const w = this.container.clientWidth;
    const h = this.container.clientHeight;
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h);
  }

  destroy() {
    window.removeEventListener('resize', this.onResize);
    if (this.escHandler) {
      window.removeEventListener('keydown', this.escHandler);
      this.escHandler = null;
    }
    if (this.container && this.container.classList.contains('maximized')) {
      const pricePanel = document.getElementById('pricePanel');
      if (pricePanel) {
        pricePanel.appendChild(this.container);
      }
      this.container.classList.remove('maximized');
    }
    document.body.classList.remove('chart-3d-maximized-body');
    if (this.animFrameId) {
      cancelAnimationFrame(this.animFrameId);
      this.animFrameId = null;
    }
    if (this.controls) {
      this.controls.dispose();
      this.controls = null;
    }
    this.clearGroup(this.fusedGroup);
    if (this.scene) {
      this.scene.traverse((obj) => {
        if (obj.geometry) obj.geometry.dispose();
        if (obj.material) {
          if (Array.isArray(obj.material)) {
            obj.material.forEach(m => m.dispose());
          } else {
            obj.material.dispose();
          }
        }
      });
      this.scene = null;
    }
    if (this.renderer) {
      this.renderer.dispose();
      if (this.renderer.domElement && this.renderer.domElement.parentNode) {
        this.renderer.domElement.parentNode.removeChild(this.renderer.domElement);
      }
      this.renderer = null;
    }
  }

  createMaximizeButton() {
    const btn = document.createElement('button');
    btn.className = 'btn-3d-maximize';
    btn.type = 'button';
    btn.title = 'Fullscreen';
    btn.innerHTML = `
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>
      </svg>
    `;
    
    btn.style.position = 'absolute';
    btn.style.top = '15px';
    btn.style.right = '15px';
    btn.style.zIndex = '100';
    btn.style.background = 'rgba(15, 23, 42, 0.75)';
    btn.style.border = '1px solid rgba(255, 255, 255, 0.15)';
    btn.style.borderRadius = '8px';
    btn.style.color = '#f8fafc';
    btn.style.padding = '8px';
    btn.style.display = 'flex';
    btn.style.alignItems = 'center';
    btn.style.justifyContent = 'center';
    btn.style.cursor = 'pointer';
    btn.style.backdropFilter = 'blur(8px)';
    btn.style.transition = 'all 0.2s';

    btn.addEventListener('mouseenter', () => {
      btn.style.background = 'rgba(30, 41, 59, 0.9)';
      btn.style.borderColor = 'rgba(255, 255, 255, 0.3)';
    });
    btn.addEventListener('mouseleave', () => {
      btn.style.background = 'rgba(15, 23, 42, 0.75)';
      btn.style.borderColor = 'rgba(255, 255, 255, 0.15)';
    });

    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      this.toggleMaximize();
    });

    this.container.appendChild(btn);
    this.maximizeBtn = btn;

    this.escHandler = (e) => {
      if (e.key === 'Escape' && this.container.classList.contains('maximized')) {
        this.toggleMaximize();
      }
    };
    window.addEventListener('keydown', this.escHandler);
  }

  toggleMaximize() {
    const isMaximized = !this.container.classList.contains('maximized');
    
    if (isMaximized) {
      document.body.appendChild(this.container);
      this.container.classList.add('maximized');
      document.body.classList.add('chart-3d-maximized-body');
    } else {
      const pricePanel = document.getElementById('pricePanel');
      if (pricePanel) {
        pricePanel.appendChild(this.container);
      }
      this.container.classList.remove('maximized');
      document.body.classList.remove('chart-3d-maximized-body');
    }
    
    if (this.maximizeBtn) {
      if (isMaximized) {
        this.maximizeBtn.innerHTML = `
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M4 14h6v6M20 10h-6V4M14 10l7-7M10 14l-7 7"/>
          </svg>
        `;
        this.maximizeBtn.title = "Exit Fullscreen";
      } else {
        this.maximizeBtn.innerHTML = `
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>
          </svg>
        `;
        this.maximizeBtn.title = "Fullscreen";
      }
    }

    this.resize();
    setTimeout(() => this.resize(), 50);
  }
}

class Stock3DSubChart {
  constructor(container, payload, paneIndicators) {
    this.container = container;
    this.payload = payload;
    this.paneIndicators = paneIndicators || [];
    this.animFrameId = null;
    this.onResize = this.resize.bind(this);
    this.init();
  }

  init() {
    const candles = this.payload.candles || [];
    if (candles.length === 0) {
      this.container.innerHTML = '<div style="color:var(--muted);padding:40px;text-align:center;">No candle data available for 3D visualization.</div>';
      return;
    }

    // Build timeline index map
    this.timeToIdxMap = new Map();
    candles.forEach((c, i) => {
      this.timeToIdxMap.set(normalizeTime(c.time), i);
    });

    const xStep = 0.78;
    const x0 = -candles.length * xStep / 2;
    this.x = (i) => x0 + i * xStep;

    // Three.js Core Setup
    this.scene = new THREE.Scene();
    this.scene.fog = new THREE.Fog(0x070d18, 65, 155);

    const w = this.container.clientWidth || 800;
    const h = this.container.clientHeight || 320;
    this.camera = new THREE.PerspectiveCamera(45, w / h, 0.1, 1000);
    this.camera.position.set(0, 25, 65);

    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setSize(w, h);
    
    // Clear container and append renderer element
    this.container.innerHTML = '';
    this.container.appendChild(this.renderer.domElement);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.target.set(0, 0, 0);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.06;
    this.controls.minDistance = 15;
    this.controls.maxDistance = 150;
    this.controls.maxPolarAngle = Math.PI * 0.49;
    this.controls.update();

    // Lighting
    this.scene.add(new THREE.AmbientLight(0x9db8ff, 0.75));
    const key = new THREE.DirectionalLight(0xffffff, 1.5);
    key.position.set(15, 40, 25);
    this.scene.add(key);

    this.root = new THREE.Group();
    this.scene.add(this.root);

    this.dynamicIndicatorsGroup = new THREE.Group();
    this.root.add(this.dynamicIndicatorsGroup);

    // Ground Grid
    const grid = new THREE.GridHelper(150, 75, 0x355074, 0x1b2b45);
    grid.position.y = -14;
    this.root.add(grid);

    // Render indicators
    this.renderPaneIndicators();
    this.createMaximizeButton();
    this.createLegendOverlay();

    window.addEventListener('resize', this.onResize);
    
    // Animation Loop
    const animate = () => {
      this.controls.update();
      this.renderer.render(this.scene, this.camera);
      this.animFrameId = requestAnimationFrame(animate);
    };
    animate();
  }

  updatePaneIndicators(paneIndicators) {
    this.paneIndicators = paneIndicators || [];
    this.renderPaneIndicators();
    this.updateLegend();
  }

  createLegendOverlay() {
    const legend = document.createElement('div');
    legend.className = 'chart-3d-legend';
    
    legend.style.position = 'absolute';
    legend.style.top = '15px';
    legend.style.left = '15px';
    legend.style.zIndex = '100';
    legend.style.background = 'rgba(15, 23, 42, 0.6)';
    legend.style.border = '1px solid rgba(255, 255, 255, 0.1)';
    legend.style.borderRadius = '10px';
    legend.style.color = '#e2e8f0';
    legend.style.padding = '10px 14px';
    legend.style.fontFamily = 'Inter, sans-serif';
    legend.style.fontSize = '12px';
    legend.style.lineHeight = '1.5';
    legend.style.pointerEvents = 'none';
    legend.style.backdropFilter = 'blur(6px)';
    legend.style.display = 'flex';
    legend.style.flexDirection = 'column';
    legend.style.gap = '4px';

    this.container.appendChild(legend);
    this.legendEl = legend;
    this.updateLegend();
  }

  updateLegend() {
    if (!this.legendEl) return;
    
    const formatValue = (val) => val !== null && val !== undefined ? Number(val).toFixed(3) : 'N/A';

    let html = `
      <div style="font-weight: 700; color: #f8fafc; font-size: 13px; margin-bottom: 2px;">
        Sub Indicators / 副图指标
      </div>
    `;

    if (this.paneIndicators && this.paneIndicators.length > 0) {
      let indicatorHtml = '';
      this.paneIndicators.forEach((item) => {
        (item.series || []).forEach((seriesDef) => {
          if (Array.isArray(seriesDef.data) && seriesDef.data.length > 0) {
            const lastPoint = seriesDef.data[seriesDef.data.length - 1];
            if (lastPoint && lastPoint.value !== null && lastPoint.value !== undefined) {
              const name = seriesDef.name || item.name || 'Indicator';
              const color = seriesDef.color || '#38bdf8';
              indicatorHtml += `<span style="color: ${color}">${name}: <strong>${formatValue(lastPoint.value)}</strong></span> `;
            }
          }
        });
      });
      if (indicatorHtml) {
        html += `<div style="display: flex; gap: 10px; flex-wrap: wrap; margin-top: 2px;">${indicatorHtml}</div>`;
      }
    } else {
      html += `<div style="color: var(--muted);">No active indicators</div>`;
    }

    this.legendEl.innerHTML = html;
  }

  renderPaneIndicators() {
    if (!this.dynamicIndicatorsGroup) return;

    // Clear existing children
    while (this.dynamicIndicatorsGroup.children.length > 0) {
      const obj = this.dynamicIndicatorsGroup.children[0];
      this.dynamicIndicatorsGroup.remove(obj);
      if (obj.geometry) obj.geometry.dispose();
      if (obj.material) {
        if (Array.isArray(obj.material)) {
          obj.material.forEach(m => m.dispose());
        } else {
          obj.material.dispose();
        }
      }
    }

    if (!this.paneIndicators || this.paneIndicators.length === 0) return;

    // Find dynamic min and max across all series
    const allVals = [];
    this.paneIndicators.forEach((item) => {
      (item.series || []).forEach((seriesDef) => {
        (seriesDef.data || []).forEach((d) => {
          if (d.value !== null && d.value !== undefined) {
            allVals.push(d.value);
          }
        });
      });
    });

    const minVal = allVals.length > 0 ? Math.min(...allVals) : -1;
    const maxVal = allVals.length > 0 ? Math.max(...allVals) : 1;
    const valDiff = maxVal - minVal;
    const scaleY = valDiff > 0 ? 22 / valDiff : 1;
    
    this.y = (v) => (v - minVal) * scaleY - 11;

    let yZero = -11;
    if (minVal <= 0 && maxVal >= 0) {
      yZero = (0 - minVal) * scaleY - 11;
    } else if (maxVal < 0) {
      yZero = 11;
    }

    // Materials cache to avoid duplicate instantiation
    const upHistogramMat = new THREE.MeshStandardMaterial({ color: 0x22c55e, emissive: 0x083f29, metalness: 0.2, roughness: 0.4 });
    const downHistogramMat = new THREE.MeshStandardMaterial({ color: 0xef4444, emissive: 0x4a0914, metalness: 0.2, roughness: 0.4 });

    let seriesIdx = 0;
    const baseZ = 0;
    const zSpacing = 0.5;

    this.paneIndicators.forEach((item) => {
      (item.series || []).forEach((seriesDef) => {
        if (!Array.isArray(seriesDef.data) || seriesDef.data.length === 0) return;

        const zVal = baseZ + seriesIdx * zSpacing;
        seriesIdx++;

        if (seriesDef.type === 'histogram') {
          // Draw as 3D boxes
          seriesDef.data.forEach((d) => {
            const t = normalizeTime(d.time);
            const idx = this.timeToIdxMap.get(t);
            if (idx !== undefined && d.value !== null && d.value !== undefined) {
              const valY = this.y(d.value);
              const height = Math.max(Math.abs(valY - yZero), 0.25);
              const center = (valY + yZero) / 2;
              
              const geom = new THREE.BoxGeometry(0.45, height, 0.5);
              const mat = d.value >= 0 ? upHistogramMat : downHistogramMat;
              const box = new THREE.Mesh(geom, mat);
              box.position.set(this.x(idx), center, zVal);
              this.dynamicIndicatorsGroup.add(box);
            }
          });
        } else {
          // Draw as 3D Line
          const pts = [];
          seriesDef.data.forEach((d) => {
            const t = normalizeTime(d.time);
            const idx = this.timeToIdxMap.get(t);
            if (idx !== undefined && d.value !== null && d.value !== undefined) {
              pts.push(new THREE.Vector3(this.x(idx), this.y(d.value), zVal));
            }
          });

          if (pts.length > 1) {
            const geo = new THREE.BufferGeometry().setFromPoints(pts);
            let colorVal = 0x38bdf8;
            if (seriesDef.color) {
              try {
                colorVal = new THREE.Color(seriesDef.color);
              } catch (e) {
                // fallback
              }
            }
            const mat = new THREE.LineBasicMaterial({
              color: colorVal,
              transparent: true,
              opacity: 0.92,
              linewidth: 2
            });
            const line = new THREE.Line(geo, mat);
            this.dynamicIndicatorsGroup.add(line);
          }
        }
      });
    });
  }

  createMaximizeButton() {
    const btn = document.createElement('button');
    btn.className = 'btn-3d-maximize';
    btn.type = 'button';
    btn.title = 'Fullscreen';
    btn.innerHTML = `
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>
      </svg>
    `;
    
    btn.style.position = 'absolute';
    btn.style.top = '15px';
    btn.style.right = '15px';
    btn.style.zIndex = '100';
    btn.style.background = 'rgba(15, 23, 42, 0.75)';
    btn.style.border = '1px solid rgba(255, 255, 255, 0.15)';
    btn.style.borderRadius = '8px';
    btn.style.color = '#f8fafc';
    btn.style.padding = '8px';
    btn.style.display = 'flex';
    btn.style.alignItems = 'center';
    btn.style.justifyContent = 'center';
    btn.style.cursor = 'pointer';
    btn.style.backdropFilter = 'blur(8px)';
    btn.style.transition = 'all 0.2s';

    btn.addEventListener('mouseenter', () => {
      btn.style.background = 'rgba(30, 41, 59, 0.9)';
      btn.style.borderColor = 'rgba(255, 255, 255, 0.3)';
    });
    btn.addEventListener('mouseleave', () => {
      btn.style.background = 'rgba(15, 23, 42, 0.75)';
      btn.style.borderColor = 'rgba(255, 255, 255, 0.15)';
    });

    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      this.toggleMaximize();
    });

    this.container.appendChild(btn);
    this.maximizeBtn = btn;

    this.escHandler = (e) => {
      if (e.key === 'Escape' && this.container.classList.contains('maximized')) {
        this.toggleMaximize();
      }
    };
    window.addEventListener('keydown', this.escHandler);
  }

  toggleMaximize() {
    const isMaximized = !this.container.classList.contains('maximized');
    
    if (isMaximized) {
      document.body.appendChild(this.container);
      this.container.classList.add('maximized');
      document.body.classList.add('chart-3d-maximized-body');
    } else {
      const customPanel = document.getElementById('customIndicatorPanel');
      if (customPanel) {
        customPanel.appendChild(this.container);
      }
      this.container.classList.remove('maximized');
      document.body.classList.remove('chart-3d-maximized-body');
    }
    
    if (this.maximizeBtn) {
      if (isMaximized) {
        this.maximizeBtn.innerHTML = `
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M4 14h6v6M20 10h-6V4M14 10l7-7M10 14l-7 7"/>
          </svg>
        `;
        this.maximizeBtn.title = "Exit Fullscreen";
      } else {
        this.maximizeBtn.innerHTML = `
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>
          </svg>
        `;
        this.maximizeBtn.title = "Fullscreen";
      }
    }

    this.resize();
    setTimeout(() => this.resize(), 50);
  }

  resize() {
    if (!this.container || !this.renderer || !this.camera) return;
    const w = this.container.clientWidth;
    const h = this.container.clientHeight;
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h);
  }

  destroy() {
    window.removeEventListener('resize', this.onResize);
    if (this.escHandler) {
      window.removeEventListener('keydown', this.escHandler);
      this.escHandler = null;
    }
    if (this.container && this.container.classList.contains('maximized')) {
      const customPanel = document.getElementById('customIndicatorPanel');
      if (customPanel) {
        customPanel.appendChild(this.container);
      }
      this.container.classList.remove('maximized');
    }
    document.body.classList.remove('chart-3d-maximized-body');
    if (this.animFrameId) {
      cancelAnimationFrame(this.animFrameId);
      this.animFrameId = null;
    }
    if (this.controls) {
      this.controls.dispose();
      this.controls = null;
    }
    if (this.scene) {
      this.scene.traverse((obj) => {
        if (obj.geometry) obj.geometry.dispose();
        if (obj.material) {
          if (Array.isArray(obj.material)) {
            obj.material.forEach(m => m.dispose());
          } else {
            obj.material.dispose();
          }
        }
      });
      this.scene = null;
    }
    if (this.renderer) {
      this.renderer.dispose();
      if (this.renderer.domElement && this.renderer.domElement.parentNode) {
        this.renderer.domElement.parentNode.removeChild(this.renderer.domElement);
      }
      this.renderer = null;
    }
  }
}

window.Stock3DChart = Stock3DChart;
window.Stock3DSubChart = Stock3DSubChart;
