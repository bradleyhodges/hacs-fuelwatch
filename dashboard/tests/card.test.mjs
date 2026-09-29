import {test} from 'node:test';
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';

const window = new Window();
for (const name of ['window','document','customElements','HTMLElement','navigator']) {
  Object.defineProperty(globalThis,name,{value: name === 'window' ? window : window[name], configurable:true});
}
await import('../src/card.js');

test('card rejects missing config and registers a visual editor', async () => {
  const card = document.createElement('fuelwatch-wa-card');
  assert.throws(() => card.setConfig(null));
  card.setConfig({type:'custom:fuelwatch-wa-card'});
  assert.ok(await card.constructor.getConfigElement());
});

test('card displays zero cost and safely renders station text', async () => {
  const card=document.createElement('fuelwatch-wa-card');
  card.setConfig({tracking_id:'home', show_map:false});
  card.hass={callWS: async () => ({profiles:[{id:'home',title:'Home',type:'search'}],products:{'1':'Unleaded'},view:{title:'Home',type:'search',groups:{'1':[{station_id:'a',name:'<img src=x onerror=alert(1)>',price:180,effective_price:180,fill_cost:0,rank:1,address:'Test',suburb:'PERTH',latitude:-31,longitude:115}]}}})};
  document.body.append(card);
  await new Promise(resolve=>setTimeout(resolve,30));
  assert.ok(card.shadowRoot.textContent.includes('$0.00'));
  assert.equal(card.shadowRoot.querySelector('img'),null);
  card.remove();
});

test('sidebar panel hosts the same card', () => {
  const panel=document.createElement('fuelwatch-wa-panel');
  document.body.append(panel);
  assert.ok(panel.shadowRoot.querySelector('fuelwatch-wa-card'));
  panel.remove();
});

test('deleted selected profile recovers to an available profile', async (t) => {
  const card=document.createElement('fuelwatch-wa-card');
  t.after(()=>card.remove());
  card.setConfig({tracking_id:'deleted', show_map:false});
  const calls=[];
  card.hass={callWS:async request=>{
    calls.push(request.tracking_id);
    if(request.tracking_id==='deleted') throw {code:'not_found', message:'Tracking entry removed'};
    return {profiles:[{id:'home', title:'Home', type:'search'}],products:{},view:null};
  }};
  document.body.append(card);
  await new Promise(resolve=>setTimeout(resolve,30));
  assert.deepEqual(calls,['deleted',undefined,'home']);
  assert.equal(card.shadowRoot.querySelector('select').value,'home');
  card.remove();
});

test('station details include phone, hours, restrictions and safe provider attribution', async (t) => {
  const card=document.createElement('fuelwatch-wa-card');
  t.after(()=>card.remove());
  card.setConfig({tracking_id:'home',show_map:false});
  card.hass={callWS:async()=>({profiles:[{id:'home',title:'Home',type:'search'}],products:{'1':'Unleaded'},view:{type:'search',groups:{'1':[{
    station_id:'a',name:'Station',price:180,rank:1,address:'1 Test Road',suburb:'PERTH',state:'WA',postcode:'6000',
    phone:'+61899811151',site_features:['ATM','Toilets'],open_hours:{Monday:'06:00-20:30'},restrictions:['Membership Required'],
    enrichment:{provider:'Google Maps',google_maps_uri:'https://maps.google.com/?cid=123',stale:true,attributions:[{display_name:'Unsafe provider',uri:'javascript:alert(1)'},{display_name:'Example provider',uri:'https://example.com/'}]},
  }]}}})};
  document.body.append(card);
  await new Promise(resolve=>setTimeout(resolve,30));
  const root=card.shadowRoot;
  assert.ok(root.textContent.includes('WA 6000'));
  assert.ok(root.textContent.includes('Monday: 06:00-20:30'));
  assert.ok(root.textContent.includes('Membership Required'));
  assert.ok(root.textContent.includes('ATM'));
  assert.equal(root.querySelector('a[href="tel:+61899811151"]').textContent,'+61899811151');
  assert.ok(root.querySelector('a[href="https://maps.google.com/?cid=123"]'));
  assert.ok(root.querySelector('a[href="https://example.com/"]'));
  assert.equal(root.querySelector('a[href^="javascript:"]'),null);
});
