'use strict';

// Display aid for an existing kana reading, never a replacement for model input.
// Long vowels stay explicit (aa / ou), useful when comparing singing moras.
const romanizeReading = (() => {
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
  return value => {
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
})();
