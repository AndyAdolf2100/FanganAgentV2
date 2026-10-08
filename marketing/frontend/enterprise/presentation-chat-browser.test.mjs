// Isolated Vue component tests; all chat endpoints are intercepted offline.
import assert from 'node:assert/strict';
import {test, before, after} from 'node:test';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import vue from '@vitejs/plugin-vue';
const {chromium} = createRequire(new URL('../../presentation/package.json', import.meta.url))('playwright');
let server, browser, base;
const fixtureTemplate = `<button @click="runId='alpha'">Alpha</button><button @click="runId='beta'">Beta</button><PresentationChat :run-id="runId" :job="job" @updated="updates++"/><output aria-label="refresh count">{{updates}}</output>`;
const fixtureHtml = `<!doctype html><html><body><div id="app"></div><script type="module">
import {createApp, ref} from 'vue/dist/vue.esm-bundler.js';
import PresentationChat from '/enterprise/PresentationChat.vue';
createApp({components:{PresentationChat},setup(){return {runId:ref('alpha'),updates:ref(0),job:{id:'job',status:'running',options:{template_id:'enterprise'}}}},
template:${JSON.stringify(fixtureTemplate)}}).mount('#app');
</script></body></html>`;
before(async () => {
  server = await createServer({root: fileURLToPath(new URL('../', import.meta.url)), configFile: false,
    plugins: [vue(), {name:'chat-fixture', configureServer(vite) {vite.middlewares.use(async (req, res, next) => {
      if (req.url !== '/__chat-test') return next();
      res.setHeader('Content-Type', 'text/html');
      res.end(await vite.transformIndexHtml('/__chat-test', fixtureHtml));
    });}}], server:{host:'127.0.0.1', port:0, hmr:false}, logLevel:'error'});
  await server.listen();
  base = `http://127.0.0.1:${server.httpServer.address().port}`;
  browser = await chromium.launch({executablePath: process.env.PRESENTATION_CHROMIUM, args:['--no-sandbox']});
});
after(async () => {await browser?.close(); await server?.close();});

const snapshot = (messages = [], requests = []) => ({messages, requests, active_job_id:'job'});
const reply = (id, text, role = 'assistant') => ({id, text, role, created: 1700000000});
const json = (route, body, status = 200) => route.fulfill({status, contentType:'application/json', body:JSON.stringify(body)});
async function fixture(handler) {
  const page = await browser.newPage(), errors = [];
  page.setDefaultTimeout(10000);
  page.on('pageerror', error => errors.push(error.message));
  await page.clock.install();
  await page.route('**/api/runs/*/presentation-chat', route => handler(route, new URL(route.request().url()).pathname.split('/')[3]));
  await page.goto(base + '/__chat-test');
  await page.getByRole('region', {name:'PPT 修改对话'}).waitFor();
  return {page, errors};
}

test('running jobs accept messages, deduplicate submit, and render plain text with distinct system roles', async () => {
  const posts = [], messages = [reply('ack','上一条修改要求正在排队','system'), reply('plan','<img src=x onerror="window.chatXss=true">')];
  const {page, errors} = await fixture(route => {
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON(); posts.push(body);
      messages.push(reply('user-1', body.text, 'user'));
    }
    return json(route, snapshot(messages, [{id:'req', status:'queued'}]));
  });
  try {
    await page.getByText('上一条修改要求正在排队', {exact:true}).waitFor();
    assert.equal(await page.locator('.chat-message-system strong').innerText(), '系统');
    assert.equal(await page.locator('.chat-history img').count(), 0);
    assert.equal(await page.evaluate(() => window.chatXss), undefined);
    await page.getByLabel('修改要求', {exact:true}).fill('第八页的圆环放大，文字保持清楚');
    await page.getByLabel('指定页码（可选）').fill('8,13-15');
    await page.locator('.chat-form').evaluate(form => {form.requestSubmit(); form.requestSubmit();});
    await page.getByText('第八页的圆环放大，文字保持清楚', {exact:true}).waitFor();
    assert.equal(posts.length, 1);
    assert.deepEqual(posts[0].page_numbers, [8, 13, 14, 15]);
    assert.equal(posts[0].job_id, 'job');
    assert.equal(typeof posts[0].client_message_id, 'string');
    await page.getByLabel('修改要求', {exact:true}).fill('第二条要求也可以继续发送');
    assert.equal(await page.getByRole('button', {name:'发送修改要求'}).isEnabled(), true);
    assert.deepEqual(errors, []);
  } finally {await page.close();}
});

test('validation and failed submission retain input; retry reuses the idempotency key', async () => {
  const posts = [];
  const {page, errors} = await fixture(route => {
    if (route.request().method() === 'GET') return json(route, snapshot());
    posts.push(route.request().postDataJSON());
    return posts.length === 1 ? json(route, {detail:'暂时无法接收消息'}, 503) : json(route, {request_id:'accepted'});
  });
  try {
    await page.getByLabel('修改要求', {exact:true}).fill('让第十三页居中');
    await page.getByLabel('指定页码（可选）').fill('13-2');
    await page.getByRole('button', {name:'发送修改要求'}).click();
    await page.getByRole('alert').filter({hasText:'从小到大'}).waitFor();
    assert.equal(posts.length, 0);
    await page.getByLabel('指定页码（可选）').fill('13');
    await page.getByRole('button', {name:'发送修改要求'}).click();
    await page.getByRole('alert').filter({hasText:'输入内容已保留'}).waitFor();
    assert.equal(await page.getByLabel('修改要求', {exact:true}).inputValue(), '让第十三页居中');
    assert.equal(await page.getByLabel('指定页码（可选）').inputValue(), '13');
    await page.getByRole('button', {name:'发送修改要求'}).click();
    await page.getByText('消息已发送。Agent 的修改计划和处理结果会显示在这里。', {exact:false}).waitFor();
    assert.equal(posts.length, 2);
    assert.equal(posts[0].client_message_id, posts[1].client_message_id);
    assert.equal(await page.getByLabel('修改要求', {exact:true}).inputValue(), '');
    assert.deepEqual(errors, []);
  } finally {await page.close();}
});

test('three-second polling updates plans and completion and notifies the parent', async () => {
  let gets = 0;
  const statuses = ['queued','planned','completed'];
  const {page, errors} = await fixture(route => {
    const status = statuses[Math.min(gets++, statuses.length - 1)];
    return json(route, snapshot([reply(status, `处理状态：${status}`)], [{id:'req', status}]));
  });
  try {
    await page.getByText('处理状态：queued', {exact:true}).waitFor();
    await page.clock.fastForward(3000);
    await page.getByText('处理状态：planned', {exact:true}).waitFor();
    await page.clock.fastForward(3000);
    await page.getByText('处理状态：completed', {exact:true}).waitFor();
    assert.equal(gets, 3);
    assert.equal(Number(await page.getByLabel('refresh count').innerText()), 3);
    assert.deepEqual(errors, []);
  } finally {await page.close();}
});

test('switching projects ignores an old GET and keeps failed drafts separate', async () => {
  let delayed, alphaGets = 0;
  const {page, errors} = await fixture((route, project) => {
    if (project === 'alpha' && route.request().method() === 'GET' && ++alphaGets === 1) {delayed = route; return;}
    if (route.request().method() === 'POST') return json(route, {detail:'发送失败'}, 503);
    return json(route, snapshot([reply(project, `${project} 的对话`)]));
  });
  try {
    await page.getByLabel('修改要求', {exact:true}).fill('alpha 的待发送要求');
    await page.getByRole('button', {name:'Beta', exact:true}).click();
    await page.getByText('beta 的对话', {exact:true}).waitFor();
    assert.equal(await page.getByLabel('修改要求', {exact:true}).inputValue(), '');
    await json(delayed, snapshot([reply('old','过期 alpha 回复')])).catch(() => {});
    assert.equal(await page.getByText('过期 alpha 回复', {exact:true}).count(), 0);
    await page.getByRole('button', {name:'Alpha', exact:true}).click();
    await page.getByText('alpha 的对话', {exact:true}).waitFor();
    assert.equal(await page.getByLabel('修改要求', {exact:true}).inputValue(), 'alpha 的待发送要求');
    await page.getByRole('button', {name:'发送修改要求'}).click();
    await page.getByRole('alert').filter({hasText:'输入内容已保留'}).waitFor();
    await page.getByRole('button', {name:'Beta', exact:true}).click();
    await page.getByText('beta 的对话', {exact:true}).waitFor();
    assert.equal(await page.getByRole('alert').count(), 0);
    assert.equal(await page.getByLabel('修改要求', {exact:true}).inputValue(), '');
    assert.deepEqual(errors, []);
  } finally {await page.close();}
});

test('switching projects during POST cannot clear or overwrite the new draft', async () => {
  let pendingPost;
  const {page, errors} = await fixture((route, project) => {
    if (route.request().method() === 'POST') {pendingPost = route; return;}
    return json(route, snapshot([reply(project, `${project} 的对话`)]));
  });
  try {
    await page.getByText('alpha 的对话', {exact:true}).waitFor();
    await page.getByLabel('修改要求', {exact:true}).fill('alpha 的修改');
    const sent = page.waitForRequest(request => request.method() === 'POST');
    await page.getByRole('button', {name:'发送修改要求'}).click(); await sent;
    await page.getByRole('button', {name:'Beta', exact:true}).click();
    await page.getByText('beta 的对话', {exact:true}).waitFor();
    await page.getByLabel('修改要求', {exact:true}).fill('beta 的新要求');
    await json(pendingPost, snapshot([reply('late','alpha 的迟到结果')])).catch(() => {});
    assert.equal(await page.getByLabel('修改要求', {exact:true}).inputValue(), 'beta 的新要求');
    assert.equal(await page.getByText('alpha 的迟到结果', {exact:true}).count(), 0);
    assert.equal(await page.getByRole('button', {name:'发送修改要求'}).isEnabled(), true);
    assert.deepEqual(errors, []);
  } finally {await page.close();}
});
