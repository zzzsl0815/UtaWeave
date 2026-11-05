const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const context=vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/song2voice/web/static/romaji.js'),'utf8'),context);
const from=value=>vm.runInContext(`romajiToReading(${JSON.stringify(value)})`,context);
const to=value=>vm.runInContext(`readingToRomajiInput(${JSON.stringify(value)})`,context);

test('romaji input supports moras, gemination, nasal boundaries and long vowels',()=>{
  for(const [input,reading] of [['kimi','キミ'],['tsu gi ha gi','ツ ギ ハ ギ'],['gakkou','ガッコウ'],
    ['matcha','マッチャ'],["shin'you",'シンヨウ'],['shinyou','シニョウ'],['konnichiwa','コンニチワ'],
    ['nn','ン'],['onna','オンナ'],['xtsu','ッ'],['sūpā','スウパア'],['ko-hi-','コーヒー']])assert.equal(from(input),reading);
});
test('English and score controls have explicit meaning, not language guesses',()=>{
  assert.equal(from('kimi {tick tack}'),'キミ tick tack');
  assert.equal(from('{no} no'),'no ノ');
  for(const control of ['SP','AP','-'])assert.equal(from(control),control);
  for(const input of ['tick','k','{tick','{}','{<script>}','kimi}'])assert.throws(()=>from(input));
});
test('editable romaji preserves kana spellings and English when round-tripped',()=>{
  for(const text of ['ツ ギ ハ ギ','キミ tick tack','キミ no','キャット','スーパー',
    'ヂ ヅ ヲ ヰ ヱ ッ ャ ー','ティ ディ トゥ ドゥ','シンヨウ','SP','-'])assert.equal(from(to(text)),text);
});
