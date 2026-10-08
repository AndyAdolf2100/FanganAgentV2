import assert from 'node:assert/strict';
import {test} from 'node:test';
import {chatAuthor, chatTime, parseChatPages} from './presentation-chat.js';

test('page selection accepts ranges, Chinese separators, spacing and removes duplicates', () => {
  assert.deepEqual(parseChatPages('8, 13-15，14、2 – 3'), [2, 3, 8, 13, 14, 15]);
  assert.deepEqual(parseChatPages(''), []);
  assert.deepEqual(parseChatPages('   '), []);
  assert.equal(parseChatPages('1-200').length, 200);
});

test('invalid page selections fail before submission', () => {
  for (const value of ['0', '201', '9-2', '-2', '1.5', '第一页', '1-', '1,', '1-999999999999999']) {
    assert.throws(() => parseChatPages(value), Error, value);
  }
});

test('system acknowledgments are distinguishable from agent replies', () => {
  assert.equal(chatAuthor({role: 'user'}), '你');
  assert.equal(chatAuthor({role: 'assistant'}), 'Agent');
  assert.equal(chatAuthor({role: 'system'}), '系统');
  assert.equal(chatAuthor({role: 'assistant', author: 'system'}), '系统');
  assert.equal(chatAuthor({role: 'assistant', source: 'system'}), '系统');
  assert.equal(chatAuthor({role: 'unknown'}), '系统');
  assert.equal(chatTime('not-a-date'), '');
});
