'use strict';

const assert=require('node:assert/strict');
const {merge,mergeText,codepointOffset,utf16Offset}=require('../frontend/editor-sync.js');

const original={
 version:6,head_revision:'rev_6',
 project:{id:'book_a',title:'潮汐',summary:'原简介',goal:50000},
 chapters:[{id:'a',body:'林岚😀把钥匙藏进靴子。\n她关上门。',updated:1,revision_id:'rev_a'}],
 records:[{id:'hero',name:'林岚',description:'守灯人'}],
 memory:[{id:'m',status:'candidate',note:'钥匙在靴子里'}]
};
const copy=value=>JSON.parse(JSON.stringify(value));
const cases=[];
function test(name,run){run();cases.push(name)}

test('保存回应推进版本，同时保留发送以后输入的新文字',()=>{
 const sent=copy(original);
 const current=copy(sent);current.chapters[0].body+='\n等保存时继续写。';
 const reply=copy(sent);reply.version=7;reply.head_revision='rev_7';reply.chapters[0].revision_id='rev_saved';
 const result=merge(sent,current,reply);
 assert.equal(result.conflicts.length,0);
 assert.equal(result.value.version,7);
 assert.equal(result.value.head_revision,'rev_7');
 assert.equal(result.value.chapters[0].revision_id,'rev_saved');
 assert.match(result.value.chapters[0].body,/等保存时继续写/);
});
test('独立记忆决定不阻止继续写正文',()=>{
 const local=copy(original);local.chapters[0].body+='\n她提起灯。';
 const remote=copy(original);remote.version=8;remote.memory[0].status='rejected';
 const result=merge(original,local,remote);
 assert.equal(result.conflicts.length,0);
 assert.equal(result.value.memory[0].status,'rejected');
 assert.match(result.value.chapters[0].body,/她提起灯/);
});
test('两窗口修改不同章节与新建章节可同时保留',()=>{
 const local=copy(original);local.chapters.push({id:'b',body:'本页第二章。'});
 const remote=copy(original);remote.chapters[0].body='另一窗口第一章。';remote.version=9;
 const result=merge(original,local,remote);
 assert.equal(result.conflicts.length,0);
 assert.equal(result.value.chapters.length,2);
 assert.equal(result.value.chapters[0].body,'另一窗口第一章。');
 assert.equal(result.value.chapters[1].body,'本页第二章。');
});
test('中文及emoji非重叠改稿正确合并',()=>{
 const base='甲😀乙丙丁。';
 assert.deepEqual(mergeText(base,'新甲😀乙丙丁。','甲😀乙丙丁。新尾'),{value:'新甲😀乙丙丁。新尾'});
 assert.equal(codepointOffset('甲😀乙',3),2);
 assert.equal(utf16Offset('甲😀乙',2),3);
});
test('同起点插入与删除或替换在双方向都保留冲突',()=>{
 const base='甲😀乙丙。',inserted='甲新😀乙丙。';
 for(const other of ['甲乙丙。','甲替乙丙。']){
  for(const [local,remote] of [[inserted,other],[other,inserted]]){
   assert.deepEqual(mergeText(base,local,remote),{conflict:true});
   const result=merge({body:base},{body:local},{body:remote});
   assert.equal(result.value.body,local);
   assert.deepEqual(result.conflicts,[{path:'body',base,local,remote}]);
  }
 }
});
test('删除结束边界的插入在双方向均保留全部改稿',()=>{
 const base='甲😀乙丙。',deleted='甲乙丙。',inserted='甲😀新乙丙。';
 assert.deepEqual(mergeText(base,deleted,inserted),{value:'甲新乙丙。'});
 assert.deepEqual(mergeText(base,inserted,deleted),{value:'甲新乙丙。'});
});
test('逆向三方合并只撤销本页修改并保留另一窗口新增段落',()=>{
 const before='😀章首。\n中间保持。\n本页旧末尾。';
 const after=before.replace('本页旧末尾。','本页新末尾。');
 const remote='另一页新增段落。\n'+after;
 assert.deepEqual(mergeText(after,before,remote),{value:'另一页新增段落。\n'+before});
});
test('撤销后重做保留期间收到的另一窗口改稿',()=>{
 const before='林岚😀站在门前。\n守灯人在楼下。',after='**林岚😀**站在门前。\n守灯人在楼下。';
 const current=after+'\n另一页新增钟声。';
 const undone=mergeText(after,before,current).value;
 const withNewRemote=undone.replace('守灯人在楼下。','守灯人留在楼上。');
 assert.deepEqual(mergeText(undone,current,withNewRemote),{
  value:after.replace('守灯人在楼下。','守灯人留在楼上。')+'\n另一页新增钟声。'
 });
});
test('撤销和重做位置被远端修改时拒绝合并',()=>{
 const before='林岚把钥匙留在左靴。',after='林岚把钥匙留在帽子。';
 assert.deepEqual(mergeText(after,before,'林岚把钥匙留在门外。'),{conflict:true});
 assert.deepEqual(mergeText(before,after,'林岚把钥匙留在门外。'),{conflict:true});
});
test('撤销删除或重做插入遇到同起点远端修改时拒绝合并',()=>{
 const before='甲😀乙丙。',deleted='甲乙丙。',inserted='甲新😀乙丙。';
 assert.deepEqual(mergeText(deleted,before,'甲新乙丙。'),{conflict:true});
 assert.deepEqual(mergeText(before,inserted,deleted),{conflict:true});
 assert.deepEqual(mergeText(inserted,before,'甲新乙丙。'),{value:deleted});
});
test('同一句不同修改留下冲突，两份正文均可读',()=>{
 const local=copy(original);local.chapters[0].body='林岚改成藏在帽子里。';
 const remote=copy(original);remote.chapters[0].body='林岚改成藏在门外。';
 const result=merge(original,local,remote);
 assert.equal(result.conflicts.length,1);
 assert.equal(result.conflicts[0].path,'chapters/a/body');
 assert.match(result.conflicts[0].local,/帽子/);
 assert.match(result.conflicts[0].remote,/门外/);
 const chosen=merge(original,local,remote,{'chapters/a/body':'作者整理：藏在帽子里，门外是诱饵。'});
 assert.equal(chosen.conflicts.length,0);
 assert.match(chosen.value.chapters[0].body,/诱饵/);
});
test('删除与另一窗口编辑不能静默丢失编辑',()=>{
 const local=copy(original);local.chapters=[];
 const remote=copy(original);remote.chapters[0].body+='新增文字。';
 const result=merge(original,local,remote);
 assert.equal(result.conflicts.length,1);
 assert.equal(result.conflicts[0].path,'chapters/a');
 assert.match(result.conflicts[0].remote.body,/新增文字/);
});
console.log(JSON.stringify({passed:true,cases},null,2));
