import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
const source=await readFile(new URL('../src/lib/dashboard.ts',import.meta.url),'utf8');
const {outputText}=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext}});
const {displayNumber,displayDuration,staleReport,safeDestination,leadTrend}=await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);
test('missing and nonfinite metrics never become zero',()=>{
  for(const n of [null,undefined,NaN,Infinity]) assert.equal(displayNumber(n),'Unavailable');
  assert.equal(displayNumber(0),'0');assert.equal(displayDuration(null),'Unavailable');
});
test('staleness rejects invalid and future times',()=>{
  const now=Date.parse('2026-10-01T12:00:00Z');
  const row=s=>({data:{data_through:s}});
  assert.equal(staleReport(row('2026-10-01T10:00:00Z'),30,now),false);
  for(const time of ['bad','2026-09-28T00:00:00Z','2026-10-02T00:00:00Z']) assert.equal(staleReport(row(time),30,now),true);
});
test('login return path cannot become a remote URL',()=>{
  for(const path of ['//evil.invalid','https://evil.invalid','/\\evil.invalid']) assert.equal(safeDestination(path),'/client');
  assert.equal(safeDestination('/client/accounts/a?embed=1'),'/client/accounts/a?embed=1');
});
test('comparisons exclude partial periods and tiny baselines',()=>{
  const r=(n,to_date=false)=>({data:{metrics:{new_leads:n},coverage:{contacts:true},to_date}});
  assert.equal(leadTrend(r(8),r(4)),100);assert.equal(leadTrend(r(8,true),r(4)),null);assert.equal(leadTrend(r(8),r(2)),null);
});
