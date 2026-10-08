"""Fakes shared by the agent tests: a GitHub stand-in, a brand file and a site."""
BRAND = ("slug: acme\nname: Acme\ndomain: acme.com\naliases: []\none_liner: x\ncategory: y\ncompetitors: []\n"
         "site: {repo: acme/site, base: main, root: public, code_root: web}\n")
LAYOUT = """export const metadata = { title: "Acme" };

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <head>
      </head>
      <body>{children}</body>
    </html>
  );
}
"""
HTML = '<html><head><title>Acme</title></head><body><a href="https://github.com/acme-dev">GitHub</a></body></html>'
BLOCKED = "User-agent: OAI-SearchBot\nDisallow: /\n"


class FakeGitHub:
    def __init__(self, files=None, states=("merged",)):
        self.files, self.writes, self.branches, self.prs = dict(files or {}), [], [], []
        self.states, self.polls = list(states), 0

    def read_file(self, repo, path, ref):
        return self.files.get(path)

    def list_dir(self, repo, path, ref):
        return sorted({p[len(path):].lstrip("/").split("/")[0] for p in self.files if p.startswith(path)})

    def create_branch(self, repo, base, branch):
        self.branches.append(branch)

    def write_file(self, repo, branch, path, text, message):
        self.writes.append(path)

    def open_pr(self, repo, base, head, title, body):
        self.prs.append(body)
        return "https://github.com/acme/site/pull/12"

    def pr_state(self, repo, number):
        self.polls += 1
        state = self.states[min(self.polls, len(self.states)) - 1]
        if isinstance(state, Exception):
            raise state
        return state


def site(robots=BLOCKED, html=HTML):
    """A fetch function for the live site. Mutate the returned dict to change the site mid-run."""
    pages = {"https://acme.com/robots.txt": robots, "https://acme.com/": html}

    def fetch(url):
        text = pages.get(url)
        return (200, text) if text is not None else (404, "")
    fetch.pages = pages
    return fetch
