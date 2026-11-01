const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');

const source = fs.readFileSync('static/app.js', 'utf8');
const functions = source.slice(source.indexOf('function documentUrl('), source.indexOf('async function sendChat('));
const context = { URL, document: { createElement: () => ({ removeAttribute(name) { delete this[name]; } }) } };
vm.runInNewContext(functions, context);

test('PDF links retain their original page route', () => {
  const link = context.makeSourceLink('课件', 7);
  assert.equal(link.href, '/api/document?source=%E8%AF%BE%E4%BB%B6&page=7#page=7');
  assert.equal(link.textContent, '《课件》第7页 ↗');
});

test('OSCAL links show the real control and allow only credential-free HTTPS', () => {
  const url = 'https://raw.githubusercontent.com/usnistgov/oscal-content/pinned/catalog.json';
  const link = context.makeSourceLink('NIST', null, 'ac-8', url);
  assert.equal(link.href, url);
  assert.equal(link.textContent, '《NIST》定位【ac-8】 ↗');
  for (const unsafe of ['javascript:alert(1)', 'http://example.test', 'https://user:pass@example.test', null]) {
    assert.equal(context.makeSourceLink('NIST', null, 'cp-9', unsafe).href, undefined);
  }
});
