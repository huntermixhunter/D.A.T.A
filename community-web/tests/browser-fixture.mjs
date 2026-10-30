// Local-only API fixture. Real forum SQL, synthetic Auth sessions, no hosted calls.
import { createServer } from 'node:http';
import { createDatabase, registerMember, rpcAs } from './database-fixture.mjs';

const db = await createDatabase();
const member = await registerMember(db, 'alice');
const claims = { sub: member, role: 'authenticated', email: 'alice@test.invalid', exp: Math.floor(Date.now() / 1000) + 3600 };
const token = `${Buffer.from('{"alg":"HS256","typ":"JWT"}').toString('base64url')}.${Buffer.from(JSON.stringify(claims)).toString('base64url')}.test-signature`;
const session = { access_token: token, token_type: 'bearer', expires_in: 3600, expires_at: claims.exp,
  refresh_token: 'local-test-refresh', user: { id: member, email: claims.email, aud: 'authenticated', role: 'authenticated', app_metadata: {}, user_metadata: {} } };
const server = createServer(async (request, response) => {
  response.setHeader('Content-Type', 'application/json');
  try {
    if (request.method === 'GET') { response.end(JSON.stringify(session)); return; }
    let text = '';
    for await (const chunk of request) text += chunk;
    const data = JSON.parse(text);
    const result = await rpcAs(db, data.signedIn ? member : null, data.route, data.payload, data.query);
    response.end(JSON.stringify(result));
  } catch (error) {
    response.statusCode = /^PT\d{3}$/.test(error.code) ? Number(error.code.slice(2)) : 500;
    response.end(JSON.stringify({ code: error.code, message: error.message }));
  }
});
server.listen(Number(process.argv[2]), '127.0.0.1');
