/** Locally bundled map code; only map tiles require an external request. */
import L from 'leaflet';
import leafletCSS from 'leaflet/dist/leaflet.css';

export function createMap(container,onTileError) {
  const style=document.createElement('style');style.textContent=leafletCSS+' .fw-pin{background:#087f6b;color:white;border:2px solid white;border-radius:8px;padding:5px 8px;font:700 12px system-ui;white-space:nowrap;box-shadow:0 2px 8px #0003}.fw-pin.selected{background:#233f38}';
  container.getRootNode().append(style);
  const map=L.map(container,{scrollWheelZoom:false}).setView([-31.95,115.86],11);
  let warned=false;
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a> contributors'}).on('tileerror',()=>{if(!warned){warned=true;onTileError();}}).addTo(map);
  const layer=L.layerGroup().addTo(map),markers=new Map();
  let signature='';
  return {
    update(rows,key,centre) {
      layer.clearLayers();markers.clear();
      for(const row of rows) {
        const label=document.createElement('span');label.className='fw-pin';label.textContent=Number(row[key]).toFixed(1);
        const icon=L.divIcon({className:'',html:label,iconSize:[60,30],iconAnchor:[30,15]});
        const marker=L.marker([row.latitude,row.longitude],{icon,title:row.name}).addTo(layer);
        const popup=document.createElement('div');popup.textContent=`${row.name}: ${Number(row[key]).toFixed(1)} c/L`;
        marker.bindPopup(popup);markers.set(row.station_id,marker);
      }
      if(centre)L.circle([centre.latitude,centre.longitude],{radius:centre.radius*1000,color:'#087f6b',weight:1,fillOpacity:.035}).addTo(layer);
      const next=rows.map(r=>r.station_id).join('|');
      map.invalidateSize();
      if(next!==signature){map.fitBounds(rows.map(r=>[r.latitude,r.longitude]),{padding:[35,35],maxZoom:14});signature=next;}
    },
    focus(row){map.setView([row.latitude,row.longitude],Math.max(map.getZoom(),13));markers.get(row.station_id)?.openPopup();},
    destroy(){map.remove();style.remove();},
  };
}
