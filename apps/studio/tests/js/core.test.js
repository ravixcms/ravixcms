global.self = global; global.crypto = require('crypto').webcrypto;
const C = require(require("path").join(__dirname, "../../../../static/studio/js/core.js"));
const assert = require('assert');
const specs = {limits:{sections:3,columns_per_section:3,widgets_per_column:3,total_nodes:50,max_depth:3},
  section:{fields:[{name:'gap',default:24}]}, column:{fields:[]}, meta:[{name:'template',default:'full_width'}], schema_version:2,
  widgets:[{type:'heading',title:'Heading'},{type:'inner_section',title:'Inner',container:true}]};
specs.widgets.forEach(w=>{w.specs=specs; w.fields=w.fields||[{name:'text',default:'Hi'}];});
const doc = C.emptyDocument(specs);
let s1 = C.makeSection(specs,[6,6]); assert.equal(C.insert(doc,specs,'root',0,s1,'section'),'');
const col = s1.columns[0];
const h = C.makeWidget(specs.widgets[0]); assert.equal(C.insert(doc,specs,col.id,0,h,'widget'),'');
const i1 = C.makeWidget(specs.widgets[1]); assert.equal(C.insert(doc,specs,col.id,1,i1,'widget'),'');
const i2 = C.makeWidget(specs.widgets[1]); assert.equal(C.insert(doc,specs,i1.columns[0].id,0,i2,'widget'),'');
const i3 = C.makeWidget(specs.widgets[1]); assert.equal(C.insert(doc,specs,i2.columns[0].id,0,i3,'widget'),'depth');
// move into itself refused
assert.equal(C.move(doc,specs,i1.id,i2.columns[0].id,0),'inside');
// duplicate gets fresh ids
const d = C.duplicate(doc,specs,s1.id); assert.ok(d.id && d.id!==s1.id); assert.equal(doc.sections.length,2);
// limits
assert.equal(C.insert(doc,specs,'root',0,C.makeSection(specs),'section'),'');
assert.equal(C.insert(doc,specs,'root',0,C.makeSection(specs),'section'),'sections');
// step across columns
assert.equal(C.step(doc,specs,h.id,1,'column'),'');
assert.equal(C.find(doc,h.id).parentId, s1.columns[1].id);
// style inheritance
C.setStyle(doc,s1.id,'desktop','pt',10); assert.deepEqual(C.styleValue(C.find(doc,s1.id).node,'mobile','pt'),{value:10,from:'desktop'});
C.setStyle(doc,s1.id,'tablet','pt',5); assert.equal(C.styleValue(C.find(doc,s1.id).node,'mobile','pt').from,'tablet');
C.setStyle(doc,s1.id,'tablet','pt',''); assert.equal(C.styleValue(C.find(doc,s1.id).node,'tablet','pt').from,'desktop');
// history
const H = new C.History(3); H.push('a'); H.push('b'); assert.equal(H.undo('c'),'b'); assert.equal(H.redo('b'),'c');
console.log('core ok');

// ---- more: moves keep ids, undo coalescing, labels
{
  const doc2 = C.emptyDocument(specs);
  const s = C.makeSection(specs, [6, 6]); C.insert(doc2, specs, 'root', 0, s, 'section');
  const a = C.makeWidget(specs.widgets[0]); C.insert(doc2, specs, s.columns[0].id, 0, a, 'widget');
  const b = C.makeWidget(specs.widgets[0]); C.insert(doc2, specs, s.columns[0].id, 1, b, 'widget');
  assert.equal(C.move(doc2, specs, a.id, s.columns[1].id, 0), '');
  assert.deepEqual(s.columns[0].widgets.map(w => w.id), [b.id]);
  assert.deepEqual(s.columns[1].widgets.map(w => w.id), [a.id]);
  assert.equal(C.step(doc2, specs, b.id, -1, 'list'), 'edge');
  assert.equal(C.remove(doc2, a.id), true); assert.equal(C.find(doc2, a.id), null);
  const lbl = C.label(b, 'widget', specs, {section: 'Section', column: 'Column'});
  assert.ok(lbl.startsWith('Heading'));
  const H2 = new C.History(100); H2.push('s0', 'k'); H2.push('s1', 'k'); H2.push('s2', 'k');
  assert.equal(H2.past.length, 1, 'typing in the same field merges into one undo step');
  H2.push('s3', 'other'); assert.equal(H2.past.length, 2);
}
console.log('core extra ok');
