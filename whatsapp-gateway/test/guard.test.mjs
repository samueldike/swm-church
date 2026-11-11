// Unit check of whatsapp-gateway/src/guard.js Run: WA_DATA_DIR=$(mktemp -d) node test/guard.test.mjs
import assert from 'node:assert/strict';
import * as G from '../src/guard.js';

await G.load();
const id = 'test';
// 1. per-minute message limit
for (let i = 0; i < G.LIMITS.msg.minute; i++) { G.check(id, ['msg']); G.record(id, ['msg']); }
assert.throws(() => G.check(id, ['msg']), (e) => e.status === 429 && /per minute/.test(e.message) && Boolean(e.retryAt));
// 2. cold first messages: tiny allowance
const id2 = 'cold';
for (let i = 0; i < G.LIMITS.cold.hour; i++) { G.check(id2, ['msg', 'cold'], 1, { risky: true }); G.record(id2, ['msg', 'cold']); }
assert.throws(() => G.check(id2, ['msg', 'cold'], 1, { risky: true }), /not saved contacts/);
// 3. the brake: WhatsApp push-back pauses risky sends but not replies
const id3 = 'brake';
G.failure(id3, 'rate-overlimit');
assert.throws(() => G.check(id3, ['msg', 'bulk'], 1, { risky: true }), /paused for safety/i);
G.check(id3, ['msg']);                                    // a reply to an existing chat still works
assert.ok(G.usage(id3).pausedUntil);
G.resume(id3);
G.check(id3, ['msg', 'bulk'], 1, { risky: true });
// 4. repeated failures also brake (1 h)
const id4 = 'fails';
for (let i = 0; i < 5; i++) G.failure(id4, 'chat not found');
assert.throws(() => G.check(id4, ['bulk'], 1, { risky: true }), /several messages in a row failed/);
// 5. group adds count per person
assert.throws(() => G.check('grp', ['group_add'], G.LIMITS.group_add.hour + 1, { risky: true }), /people added to groups/);
// 6. survives a restart
await new Promise((r) => setTimeout(r, 1500));
await G.load();
assert.equal(G.usage(id).used.msg.minute, G.LIMITS.msg.minute);
console.log('guard: all checks passed');
