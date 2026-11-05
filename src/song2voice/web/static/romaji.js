'use strict';

// Pronunciation display and explicit romaji input. Model input remains kana / English.
// Long vowels stay explicit (aa / ou), useful when comparing singing moras.
const readingTools = (() => {
  const kana = {};
  for (const [letters, sounds] of [
    ['あいうえお','a i u e o'], ['かきくけこ','ka ki ku ke ko'],
    ['がぎぐげご','ga gi gu ge go'], ['さしすせそ','sa shi su se so'],
    ['ざじずぜぞ','za ji zu ze zo'], ['たちつてと','ta chi tsu te to'],
    ['だぢづでど','da ji zu de do'], ['なにぬねの','na ni nu ne no'],
    ['はひふへほ','ha hi fu he ho'], ['ばびぶべぼ','ba bi bu be bo'],
    ['ぱぴぷぺぽ','pa pi pu pe po'], ['まみむめも','ma mi mu me mo'],
    ['やゆよ','ya yu yo'], ['らりるれろ','ra ri ru re ro'],
    ['わゐゑを','wa wi we o'], ['ぁぃぅぇぉ','a i u e o'],
    ['ゃゅょゎゕゖゔ','ya yu yo wa ka ke vu']
  ]) [...letters].forEach((letter,i) => { kana[letter] = sounds.split(' ')[i]; });
  for (const [letter, stem] of Object.entries({き:'ky',ぎ:'gy',し:'sh',じ:'j',ち:'ch',ぢ:'j',に:'ny',ひ:'hy',び:'by',ぴ:'py',み:'my',り:'ry'})) {
    for (const [small,vowel] of Object.entries({ゃ:'a',ゅ:'u',ょ:'o'})) kana[letter+small] = stem+vowel;
  }
  Object.assign(kana, {'いぇ':'ye','うぃ':'wi','うぇ':'we','うぉ':'wo','しぇ':'she','じぇ':'je','ちぇ':'che',
    'ふぁ':'fa','ふぃ':'fi','ふぇ':'fe','ふぉ':'fo','ふゅ':'fyu','てぃ':'ti','でぃ':'di','とぅ':'tu','どぅ':'du',
    'てゅ':'tyu','でゅ':'dyu','つぁ':'tsa','つぃ':'tsi','つぇ':'tse','つぉ':'tso',
    'くぁ':'kwa','くぃ':'kwi','くぇ':'kwe','くぉ':'kwo','くゎ':'kwa','ぐぁ':'gwa','ぐゎ':'gwa',
    'ゔぁ':'va','ゔぃ':'vi','ゔぇ':'ve','ゔぉ':'vo','ゔゅ':'vyu','ヷ':'va','ヸ':'vi','ヹ':'ve','ヺ':'vo'});
  const romanize = value => {
    const input=String(value || '').trim();
    if (['SP','AP','-'].includes(input)) return {text:{SP:'休止（SP）',AP:'换气（AP）','-':'延音（继续上一音）'}[input],needsReading:false};
    const text=input.normalize('NFKC').replace(/[ァ-ヶ]/g,c=>String.fromCharCode(c.charCodeAt(0)-0x60));
    const parts=[];let vowel='',needsReading=false;
    const nextSound=at=>kana[text.slice(at,at+2)] || kana[text[at]] || '';
    for (let i=0;i<text.length;) {
      const c=text[i];
      if (c==='っ') {
        let next=i+1;while (/\s/.test(text[next] || '') && next<text.length) next++;
        const sound=nextSound(next);
        parts.push(sound.startsWith('ch')?'t':/^[bcdfghjklmpqrstvwz]/.test(sound)?sound[0]:'（促音：短停顿）');
        vowel='';i++;continue;
      }
      if (c==='ん') {parts.push(/^[aeiouy]/.test(nextSound(i+1))?"n'":'n');vowel='';i++;continue;}
      if (c==='ー') {parts.push(vowel || '（长音）');i++;continue;}
      const pair=text.slice(i,i+2),sound=kana[pair] || kana[c];
      if (sound) {parts.push(sound);vowel=sound.match(/[aeiou]$/)?.[0] || '';i+=kana[pair]?2:1;continue;}
      if (/[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}]/u.test(c)) {
        if(parts.at(-1)!=='〔待转换读音〕')parts.push('〔待转换读音〕');needsReading=true;
      } else parts.push(c);
      if(!/\s/.test(c))vowel='';i++;
    }
    return {text:parts.join(''),needsReading};
  };
  const katakana=s=>s.replace(/[ぁ-ゖ]/g,c=>String.fromCharCode(c.charCodeAt(0)+0x60));
  const syllables={};
  for(const [letters,sound] of Object.entries(kana))if(!(sound in syllables))syllables[sound]=katakana(letters);
  Object.assign(syllables,{si:'シ',zi:'ジ',hu:'フ',wo:'ヲ',di:'ヂ',du:'ヅ',thi:'ティ',dhi:'ディ',twu:'トゥ',dwu:'ドゥ',
    xtsu:'ッ',ltsu:'ッ',xtu:'ッ',ltu:'ッ',xa:'ァ',xi:'ィ',xu:'ゥ',xe:'ェ',xo:'ォ',xya:'ャ',xyu:'ュ',xyo:'ョ',
    xwa:'ヮ',xka:'ヵ',xke:'ヶ',wyi:'ヰ',wye:'ヱ',n:'ン'});
  const spellings=Object.keys(syllables).sort((a,b)=>b.length-a.length);
  function convertWord(raw){
    const word=raw.toLowerCase().replace(/[āīūēōâîûêô]/g,c=>({'ā':'aa','ī':'ii','ū':'uu','ē':'ee','ō':'oo','â':'aa','î':'ii','û':'uu','ê':'ee','ô':'oo'}[c])).replace(/’/g,"'");
    let result='';
    for(let i=0;i<word.length;){
      const c=word[i],next=word[i+1];
      if(c==='n'){
        if(next==="'"){result+='ン';i+=2;continue;}
        if(next==='n'&&!/[aeiouy]/.test(word[i+2] || '')){result+='ン';i+=2;continue;}
        if(!next||!/[aeiouy]/.test(next)){result+='ン';i++;continue;}
      }
      if((/[bcdfghjklmpqrstvwz]/.test(c)&&c===next)||word.slice(i,i+3)==='tch'){
        result+='ッ';i++;continue;
      }
      const found=spellings.find(key=>word.startsWith(key,i));
      if(!found)throw Error(`无法转换“${raw}”中的拼写“${word.slice(i)}”。英文请用花括号包住，例如 {tick tack}。`);
      result+=syllables[found];i+=found.length;
    }
    return result;
  }
  function fromRomaji(value){
    const input=String(value || '').normalize('NFKC').trim();
    if(['SP','AP','-'].includes(input))return input;
    let result='';
    for(let i=0;i<input.length;){
      const c=input[i];
      if(c==='{'){
        const end=input.indexOf('}',i+1);
        if(end<0)throw Error('英文片段缺少右花括号 }');
        const english=input.slice(i+1,end).trim();
        if(!/^[a-z]+(?:['’][a-z]+)*(?:\s+[a-z]+(?:['’][a-z]+)*)*$/i.test(english))throw Error('花括号中请填写英文单词，例如 {tick tack}');
        result+=' '+english+' ';i=end+1;continue;
      }
      if(c==='}')throw Error('英文片段缺少左花括号 {');
      const word=input.slice(i).match(/^[a-zāīūēōâîûêô'’]+/i)?.[0];
      if(word){result+=convertWord(word);i+=word.length;continue;}
      result+=c==='-'?'ー':c;i++;
    }
    return result.replace(/[ \t]+/g,' ').trim();
  }
  function toRomajiInput(value){
    const input=String(value || '').normalize('NFKC').trim();
    if(['SP','AP','-'].includes(input))return input;
    return input.replace(/[a-z]+(?:['’][a-z]+)*|[ぁ-ゖァ-ヺー]+/gi,run=>{
      if(/^[a-z]/i.test(run))return '{'+run+'}';
      const roman=romanize(run).text,target=katakana(run);
      try{if(katakana(fromRomaji(roman))===target)return roman;}catch(_){}
      // Preserve spellings that share a pronunciation (ジ/ヂ, オ/ヲ),
      // and isolated small kana / held vowels, without losing the source text.
      const exact={ン:"n'",ッ:'xtsu',ー:'-',ヷ:'vuxa',ヸ:'vuxi',ヹ:'vuxe',ヺ:'vuxo'};
      return [...target].map(c=>exact[c] || spellings.find(k=>syllables[k]===c) || c).join('');
    });
  }
  return {romanize,fromRomaji,toRomajiInput};
})();
const romanizeReading=readingTools.romanize;
const romajiToReading=readingTools.fromRomaji;
const readingToRomajiInput=readingTools.toRomajiInput;
