/** FuelWatch WA: a shared sidebar dashboard and Lovelace web component.
 * All economic calculations come from the authenticated integration backend.
 * Upstream/user strings are inserted as text, never as executable HTML.
 */
const css = `
 :host{display:block;color:var(--primary-text-color,#182e2b);font-family:var(--primary-font-family,system-ui);--fw-accent:#087f6b;--fw-line:var(--divider-color,#dae5e1)}
 *{box-sizing:border-box}ha-card{display:block;border-radius:20px;overflow:hidden;background:var(--card-background-color,#fff);border:1px solid var(--fw-line)}
 .wrap{padding:24px}.eyebrow{font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--fw-accent);font-weight:750}
 .head{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:22px}h1{font-size:28px;letter-spacing:-.04em;margin:6px 0}p{margin:6px 0;line-height:1.5}.muted{color:var(--secondary-text-color,#637772);font-size:13px}
 button,select,input{font:inherit;color:inherit;border:1px solid var(--fw-line);border-radius:9px;background:var(--card-background-color,#fff);padding:10px;min-height:42px}button{cursor:pointer}button:hover{border-color:var(--fw-accent)}button:focus-visible,select:focus-visible,input:focus-visible{outline:3px solid #67bdaa;outline-offset:2px}
 .filters{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:20px}.field{display:flex;flex-direction:column;gap:6px;font-size:12px;font-weight:600;min-width:140px;flex:1}.field select,.field input{width:100%;font-size:14px;font-weight:400}
 .summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:20px}.stat{padding:18px;border:1px solid var(--fw-line);border-radius:13px}.stat:first-child{background:color-mix(in srgb,var(--fw-accent) 8%,var(--card-background-color,#fff))}.value{font-size:28px;font-weight:700;letter-spacing:-.035em;margin:8px 0 3px}.label{font-size:12px;color:var(--secondary-text-color,#637772)}
 .main{display:grid;grid-template-columns:minmax(0,1fr) minmax(300px,1fr);gap:18px}.main.no-map{grid-template-columns:1fr}.map{min-height:400px;border-radius:14px;overflow:hidden;border:1px solid var(--fw-line);z-index:0}.list{display:flex;flex-direction:column;gap:10px;max-height:520px;overflow:auto}.station{padding:14px;border:1px solid var(--fw-line);border-radius:12px;display:grid;grid-template-columns:30px minmax(0,1fr) auto;gap:10px;cursor:pointer}.station:hover,.station.active{border-color:var(--fw-accent)}.rank{width:27px;height:27px;background:var(--secondary-background-color,#edf4f1);border-radius:50%;display:grid;place-items:center;font-weight:650;font-size:12px}.station-name{font-size:14px;font-weight:650}.station-address{font-size:12px;color:var(--secondary-text-color,#637772);line-height:1.5;margin-top:4px}.price{text-align:right;font-size:22px;letter-spacing:-.04em;font-weight:700}.unit{font-size:11px;font-weight:400;letter-spacing:normal}.detail{font-size:12px;text-align:right;margin-top:6px}.badges{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}.badge{font-size:10px;padding:4px 6px;border-radius:5px;background:var(--secondary-background-color,#edf4f1)}a{color:var(--fw-accent)}.station a{font-size:12px;display:inline-block;margin-top:8px}.notice{border-radius:9px;padding:12px 14px;margin:12px 0;background:var(--secondary-background-color,#edf4f1);font-size:13px;line-height:1.5}.error{border-left:3px solid #b77b27}.empty{padding:35px 15px;text-align:center}.compare{padding:20px;border:1px solid var(--fw-line);border-radius:12px}.compare h2{font-size:17px;margin-top:0}.breakdown{width:100%;border-collapse:collapse}.breakdown td{padding:10px 0;border-bottom:1px solid var(--fw-line)}.breakdown td:last-child{text-align:right;font-weight:600}.footer{margin-top:18px;display:flex;justify-content:space-between;gap:15px;flex-wrap:wrap;font-size:11px;color:var(--secondary-text-color,#637772)}.hidden{display:none!important}.positive{color:var(--fw-accent)}.negative{color:#bb573e}
 @media(max-width:700px){.wrap{padding:16px}.main{grid-template-columns:1fr}.map{min-height:280px}.list{max-height:none}h1{font-size:25px}.summary{grid-template-columns:1fr 1fr}.value{font-size:25px}.field{min-width:125px}}
`;

/** Create DOM safely with optional CSS class and text. */
function el(tag, className, text) {
  const node=document.createElement(tag);
  if(className) node.className=className;
  if(text !== undefined) node.textContent=text;
  return node;
}
function amount(value) { return value == null || !Number.isFinite(Number(value)) ? 'Unavailable' : new Intl.NumberFormat('en-AU',{style:'currency',currency:'AUD'}).format(value); }
function numeric(value, digits=1) { return value == null ? '—' : Number(value).toFixed(digits); }
function options(control, values, selected) {
  control.replaceChildren(...values.map(([value,label])=>{const item=el('option','',label);item.value=value;return item;}));
  if(values.some(v=>v[0]===selected)) control.value=selected;
}

/** Display provider URLs only when they are ordinary HTTPS links, including older stored views. */
function attributionLink(label, value) {
  try {
    const url=new URL(value);
    if(url.protocol!=='https:' || url.username || url.password) return null;
    const link=el('a','',label);link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';
    link.onclick=event=>event.stopPropagation();return link;
  } catch { return null; }
}

/** Keep the price list compact while retaining all station facilities and local opening hours. */
function stationDetails(row) {
  const section=el('details','station-address');section.onclick=event=>event.stopPropagation();
  section.append(el('summary','','Station details'));
  if(row.trading_name && row.trading_name!==row.name) section.append(el('p','',row.trading_name));
  if(/^\+[1-9]\d{6,14}$/.test(row.phone||'')) {
    const phone=el('a','',row.phone);phone.href=`tel:${row.phone}`;section.append(phone);
  }
  if(row.is_24_hours===true) section.append(el('p','','Open 24 hours'));
  if(row.site_features?.length) section.append(el('p','',row.site_features.join(' · ')));
  for(const [day,hours] of Object.entries(row.open_hours||{})) section.append(el('p','',`${day}: ${hours} AWST`));
  if(row.restrictions?.length) section.append(el('p','',row.restrictions.join(' · ')));
  for(const value of Object.values(row.source_notes||{})) section.append(el('p','',Array.isArray(value)?value.join(' · '):value));
  return section.childElementCount>1 ? section : null;
}

export class FuelWatchCard extends HTMLElement {
  constructor() {
    super();this.attachShadow({mode:'open'});this._config={};this._period='current';this._request=0;this._lastFetch=0;
  }
  static getConfigElement(){return document.createElement('fuelwatch-wa-card-editor');}
  static getStubConfig(){return {type:'custom:fuelwatch-wa-card',show_map:true};}
  setConfig(config) {
    if(!config || typeof config!=='object') throw new Error('FuelWatch card configuration is required');
    this._config={show_map:true,...config};this._selected=config.tracking_id || this._selected;
    if(this.isConnected) this._load();
  }
  getCardSize(){return 9;}
  getGridOptions(){return {columns:24,rows:10,min_columns:6,min_rows:5};}
  set hass(value) {
    this._hass=value;
    if(this.isConnected && Date.now()-this._lastFetch>2000) this._schedule();
  }
  connectedCallback() {
    if(!this._built) this._build();
    this._load();
    this._interval=setInterval(()=>this._load(),60000);
  }
  disconnectedCallback() {
    clearInterval(this._interval);clearTimeout(this._debounce);this._request++;
    this._map?.destroy();this._map=null;
  }
  _schedule(){clearTimeout(this._debounce);this._debounce=setTimeout(()=>this._load(),250);}
  _build() {
    this._built=true;
    const style=el('style');style.textContent=css;this.shadowRoot.append(style);
    const card=el('ha-card'), wrap=el('div','wrap');card.append(wrap);this.shadowRoot.append(card);
    const head=el('div','head'), title=el('div');
    title.append(el('div','eyebrow','Western Australia · FuelWatch'),el('h1','',this._config.title || 'Find a better fill.'),el('p','muted','Local prices. Your vehicle. Clear savings.'));
    const refresh=el('button','','Refresh view');refresh.title='Reload the current data; use the integration refresh button to fetch prices';refresh.onclick=()=>this._load();
    head.append(title,refresh);wrap.append(head);
    const filters=el('div','filters');
    const createSelect=(label)=>{const field=el('label','field',label), select=el('select');field.append(select);filters.append(field);return select;};
    this._profile=createSelect('Tracking profile');this._profile.onchange=()=>{this._selected=this._profile.value;this._overrides={};this._load();};
    this._periodSelect=createSelect('Price period');options(this._periodSelect,[['current','Current prices'],['next','Next price period']],'current');
    this._periodSelect.onchange=()=>{this._period=this._periodSelect.value;this._load();};
    this._product=createSelect('Fuel product');this._product.onchange=()=>{this._chosenProduct=this._product.value;this._renderView();};
    wrap.append(filters);
    this._notice=el('div');this._summary=el('div','summary');this._main=el('div','main');this._mapNode=el('div','map');this._list=el('div','list');
    this._main.append(this._mapNode,this._list);this._comparison=el('div');
    wrap.append(this._notice,this._summary,this._main,this._comparison);
    const footer=el('div','footer');footer.append(el('span','','Prices: WA Government FuelWatch · Independent integration'),el('span','','Price changes take effect at 6 am Perth time'));
    wrap.append(footer);
  }
  async _load() {
    if(!this._hass || !this.isConnected || !this._built) return;
    const request=++this._request;this._lastFetch=Date.now();
    try {
      let data=await this._hass.callWS({type:'fuelwatch_wa/view',period:this._period,...(this._selected ? {tracking_id:this._selected}:{}),...(this._overrides||{})});
      if(request!==this._request) return;
      if(!this._selected && data.profiles?.length) {
        this._selected=(data.profiles.find(p=>p.type==='search') || data.profiles[0]).id;
        return this._load();
      }
      this._data=data;
      options(this._profile,(data.profiles||[]).map(p=>[p.id,`${p.title} · ${p.type}`]),this._selected);
      this._renderView();
    } catch(error) {
      if(request!==this._request)return;
      if(error.code==='not_found' && this._selected) {
        this._selected=null;this._overrides={};
        return this._load();
      }
      this._notice.replaceChildren(el('div','notice error',`FuelWatch could not load: ${error.message || error}. Check the integration and selected tracking entry.`));
      this._summary.replaceChildren();this._list.replaceChildren();this._comparison.replaceChildren();
      this._mapNode.classList.add('hidden');
    }
  }
  _stat(label,value,detail) {
    const card=el('div','stat');card.append(el('div','label',label),el('div','value',value));
    if(detail)card.append(el('div','muted',detail));this._summary.append(card);
  }
  _renderView() {
    const data=this._data,view=data?.view;
    this._summary.replaceChildren();this._notice.replaceChildren();this._comparison.replaceChildren();this._list.replaceChildren();
    const groups=view?.groups||{}, keys=Object.keys(groups);
    options(this._product,keys.map(p=>[p,data.products?.[p] || 'Accepted fuels']),this._chosenProduct);
    this._product.parentElement.classList.toggle('hidden',!keys.length);
    if(!view) {
      this._main.classList.add('no-map');this._mapNode.classList.add('hidden');
      const empty=el('div','empty');empty.append(el('h2','','Your next fill starts here.'),el('p','muted','Add a vehicle and a cheapest-station search in the integration settings.'));
      const link=el('a','','Open FuelWatch settings');link.href='/config/integrations/integration/fuelwatch_wa';empty.append(link);this._list.append(empty);return;
    }
    if(view.error)this._notice.append(el('div','notice error',view.error));
    if(Object.keys(data.source_errors||{}).length)this._notice.append(el('div','notice error','Some feed requests failed. Prices still within their validity period may be shown from cache; expired prices are excluded.'));
    if(view.vehicle?.error)this._notice.append(el('div','notice error',`Vehicle: ${view.vehicle.error}`));
    if(view.type==='vehicle') {
      this._stat('Litres to purchase',`${numeric(view.vehicle?.volume)} L`);
      this._stat('Fuel remaining',`${numeric(view.vehicle?.remaining)} L`);
      this._stat('Tank capacity',`${numeric(view.vehicle?.capacity)} L`);
      this._stat('Consumption',`${numeric(view.vehicle?.consumption)} L/100km`);
    }
    if(view.type==='comparison')this._renderComparison(view);
    const rows=groups[this._product.value]||[];
    const priceKey=view.price_basis==='effective'?'effective_price':'price';
    if(rows.length) {
      this._stat(this._period==='next'?'Next-period best price':'Best price',`${numeric(rows[0][priceKey])} c/L`,rows[0].name);
      this._stat(view.vehicle?.purchase_mode==='full'?'Empty-to-full estimate':'Estimated refill',amount(rows[0].fill_cost),view.vehicle ? `${numeric(view.vehicle.volume)} L · ${view.vehicle.title||'Selected vehicle'}`:'Add a vehicle profile to calculate');
      if(rows[0].valid_from)this._notice.append(el('p','muted',`Price period: ${new Date(rows[0].valid_from).toLocaleString('en-AU',{timeZone:'Australia/Perth',day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'})} AWST · ${view.price_basis==='effective'?'Eligible discounts applied':'Advertised prices'}`));
      rows.forEach(row=>this._stationRow(row,priceKey));
    } else if(keys.length) {
      this._list.append(el('div','empty',this._period==='next'?'No next-period prices available. Tomorrow’s prices are normally published from 2:30 pm Perth time.':'No valid matching prices. Check your filters and the source status.'));
    }
    const showMap=this._config.show_map!==false && rows.length>0;
    this._main.classList.toggle('no-map',!showMap);this._mapNode.classList.toggle('hidden',!showMap);
    if(showMap)this._renderMap(rows,priceKey);
    else if(this._map){this._map.destroy();this._map=null;}
  }
  _stationRow(row,priceKey) {
    const station=el('div','station');station.tabIndex=0;station.setAttribute('role','button');station.setAttribute('aria-label',`${row.name}, ${numeric(row[priceKey])} cents per litre`);
    const details=el('div'), right=el('div');details.append(el('div','station-name',row.name),el('div','station-address',`${row.address}, ${row.suburb}${row.postcode?` ${row.state||'WA'} ${row.postcode}`:''}`));
    const info=stationDetails(row);if(info)details.append(info);
    if(row.enrichment) {
      const attribution=el('div','station-address',row.enrichment.stale?'Station details awaiting refresh from ':'Station details from ');
      const provider=attributionLink('Google Maps',row.enrichment.google_maps_uri);if(provider)attribution.append(provider);
      for(const entry of row.enrichment.attributions||[]) {const link=attributionLink(entry.display_name,entry.uri);if(link)attribution.append(document.createTextNode(' · '),link);}
      details.append(attribution);
    }
    const badges=el('div','badges');
    if(row.distance_km!=null)badges.append(el('span','badge',`${numeric(row.distance_km)} km straight-line`));
    if(row.discount>0)badges.append(el('span','badge',`${numeric(row.discount)} c/L discount`));
    if(row.cached)badges.append(el('span','badge','Cached · valid period'));
    details.append(badges);
    const link=el('a','','Directions ↗');link.href=`https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(`${row.latitude},${row.longitude}`)}`;link.target='_blank';link.rel='noopener noreferrer';link.onclick=e=>e.stopPropagation();details.append(link);
    const historyEntity=Object.entries(this._hass.states||{}).find(([id,s])=>id.startsWith('sensor.') && s.attributes?.tracking_id===this._selected && s.attributes?.product===row.product && s.attributes?.rank===row.rank && s.attributes?.unit_of_measurement==='c/L' && !id.includes('next'));
    if(historyEntity){const history=el('button','','History');history.style.cssText='font-size:11px;margin-left:10px;min-height:28px;padding:4px 8px';history.onclick=e=>{e.stopPropagation();this.dispatchEvent(new CustomEvent('hass-more-info',{detail:{entityId:historyEntity[0]},bubbles:true,composed:true}));};details.append(history);}
    const price=el('div','price',numeric(row[priceKey]));price.append(el('div','unit','cents / litre'));right.append(price,el('div','detail',amount(row.fill_cost)));
    station.append(el('div','rank',String(row.rank)),details,right);
    const focus=()=>{this._list.querySelectorAll('.station').forEach(n=>n.classList.remove('active'));station.classList.add('active');this._map?.focus(row);};
    station.onclick=focus;station.onkeydown=e=>{if(e.target===station && (e.key==='Enter'||e.key===' ')){e.preventDefault();focus();}};
    this._list.append(station);
  }
  async _renderMap(rows,priceKey) {
    const generation=this._request;
    try {
      const {createMap}=await import('./map.js');
      if(!this.isConnected || generation!==this._request || this._mapNode.classList.contains('hidden')) return;
      if(!this._map)this._map=createMap(this._mapNode,()=>this._notice.append(el('div','notice','Map tiles could not load. Station prices remain available in the list.')));
      this._map.update(rows,priceKey,this._data.view.centre);
    } catch { this._mapNode.replaceChildren(el('div','empty','Map unavailable. Use the station list.')); }
  }
  _renderComparison(view) {
    const costs=view.comparison||{};
    this._stat('Estimated net saving',amount(costs.net_saving),view.worth_it==null?'Travel assumptions required':view.worth_it?'Meets your saving threshold':'Below your saving threshold');
    this._stat('Break-even purchase',`${numeric(costs.break_even_litres,2)} L`,'Fuel and optional time costs');
    const section=el('div','compare');section.append(el('h2','',`${view.station_a?.name||'Baseline'} → ${view.station_b?.name||'Alternative'}`));
    const fields=el('div','filters');
    for(const [key,label] of [['extra_km','Extra total driving (km)'],['extra_minutes','Extra journey time (minutes)']]) {
      const field=el('label','field',label), input=el('input');input.type='number';input.min='0';input.max='10000';input.step='any';input.value=this._overrides?.[key]??view.travel?.[key]??'';
      input.onchange=()=>{if(!input.checkValidity())return;this._overrides={...this._overrides};if(input.value==='')delete this._overrides[key];else this._overrides[key]=Number(input.value);this._load();};field.append(input);fields.append(field);
    }
    section.append(fields,el('p','muted','Temporary what-if values. Include the return journey where applicable. These do not change your saved comparison.'));
    const table=el('table','breakdown');
    for(const [key,label] of [['cost_a','Purchase at baseline'],['cost_b','Purchase at alternative'],['gross_saving','Gross saving'],['travel_cost','Additional fuel cost'],['time_cost','Optional time cost'],['net_saving','Net saving']]) {
      const tr=el('tr');tr.append(el('td','',label),el('td','',amount(costs[key])));table.append(tr);
    }
    section.append(table,el('p','muted','Estimate for the same planned purchase volume. Fuel consumed before arrival can change the actual pump receipt.'));this._comparison.append(section);
  }
}

class FuelWatchPanel extends HTMLElement {
  constructor(){super();this.attachShadow({mode:'open'});}
  connectedCallback(){
    if(this._card)return;
    const style=el('style');style.textContent=':host{display:block;height:100%;overflow:auto;background:var(--primary-background-color,#f3f7f5)}header{display:flex;align-items:center;gap:15px;padding:14px 22px;color:var(--primary-text-color);font:600 18px system-ui}button{font:inherit;border:0;background:none;color:inherit;cursor:pointer;padding:8px}a{margin-left:auto;font:14px system-ui;color:var(--primary-color,#087f6b)}main{max-width:1250px;margin:0 auto;padding:8px 22px 30px}@media(max-width:600px){main{padding:0 8px 20px}}';
    const header=el('header'), menu=el('button','','☰');menu.setAttribute('aria-label','Toggle sidebar');menu.onclick=()=>this.dispatchEvent(new CustomEvent('hass-toggle-menu',{bubbles:true,composed:true}));
    const settings=el('a','','Configure');settings.href='/config/integrations/integration/fuelwatch_wa';header.append(menu,el('span','','FuelWatch'),settings);
    const main=el('main');this._card=document.createElement('fuelwatch-wa-card');this._card.setConfig({show_map:true});if(this._hass)this._card.hass=this._hass;main.append(this._card);this.shadowRoot.append(style,header,main);
  }
  set hass(value){this._hass=value;if(this._card)this._card.hass=value;}
  set panel(value){this._panel=value;}
}

class FuelWatchEditor extends HTMLElement {
  constructor(){super();this.attachShadow({mode:'open'});}
  setConfig(config){this._config={...config};this._render();}
  set hass(value){this._hass=value;this._render();}
  _render(){
    if(!this._config)return;
    this.shadowRoot.replaceChildren();
    const form=document.createElement('ha-form');form.hass=this._hass;form.data=this._config;
    form.schema=[{name:'title',selector:{text:{}}},{name:'tracking_id',selector:{text:{}}},{name:'show_map',default:true,selector:{boolean:{}}}];
    form.computeLabel=s=>({title:'Title (optional)',tracking_id:'Tracking ID (optional; leave blank for a profile selector)',show_map:'Show station map'}[s.name]);
    form.addEventListener('value-changed',e=>{this._config={...this._config,...e.detail.value};this.dispatchEvent(new CustomEvent('config-changed',{detail:{config:this._config},bubbles:true,composed:true}));});
    this.shadowRoot.append(form);
  }
}

if(!customElements.get('fuelwatch-wa-card'))customElements.define('fuelwatch-wa-card',FuelWatchCard);
if(!customElements.get('fuelwatch-wa-panel'))customElements.define('fuelwatch-wa-panel',FuelWatchPanel);
if(!customElements.get('fuelwatch-wa-card-editor'))customElements.define('fuelwatch-wa-card-editor',FuelWatchEditor);
window.customCards=window.customCards||[];
if(!window.customCards.some(c=>c.type==='fuelwatch-wa-card'))window.customCards.push({type:'fuelwatch-wa-card',name:'FuelWatch WA',description:'Fuel prices, station map and vehicle savings',preview:true});
