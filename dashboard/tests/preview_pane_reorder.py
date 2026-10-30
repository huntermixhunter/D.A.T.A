"""Serve the DATA dashboard with sample chats for trying window rearrangement."""
import argparse
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arcade_preview import Handler, static_routes

DEMO = b"""<script>
window.addEventListener('DOMContentLoaded', () => {
  for (const [name, text] of [
    ['Notes', 'Drag a window by its title or dotted grip to rearrange your workspace.'],
    ['Research', 'Drop onto the highlighted window to move into its position.'],
    ['Planning', 'Your drafts, conversation and selected model move with the window.'],
    ['Drafts', 'You can also focus the dotted grip and use the arrow keys. Escape cancels a drag.']
  ]) {
    const id = ++_wsCounter;
    const path = 'C:/Example/' + name;
    _workspaces.set(id, {path, name, isMain: false, isThinking: false,
      projectNodes: [], provider: 'codex', effort: '', role: '', crew: MAIN_CHAT_CREW});
    addChatPane(id, name, path);
    const chat = document.getElementById('chat-win-ws' + id);
    chat.replaceChildren();
    appendMessageToPane(chat, 'data', text, MAIN_CHAT_CREW);
  }
  _showPaneHeaders(true);
  _updateChatGrid();
  showPanel('chat');
});
</script>"""


class PreviewHandler(Handler):
    def do_GET(self):
        if urlsplit(self.path).path in ('/', '/index.html'):
            html = (self.root / 'index.html').read_bytes()
            self.send(html.replace(b'</body>', DEMO + b'</body>'), 'text/html; charset=utf-8')
            return
        super().do_GET()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=7833)
    args = parser.parse_args()
    PreviewHandler.routes = static_routes()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), PreviewHandler)
    print(f'DATA chat layout preview: http://localhost:{args.port}/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
