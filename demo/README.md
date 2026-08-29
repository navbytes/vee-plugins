# Controls demo

Two files, one menu, every control the Vee plugin format supports —
`toggle=`, `slider=`, `progress=`, `pie=`/`donut=`/`stackedbar=`,
`sparkline=`, SF Symbols, Markdown, ANSI, images, submenus, alternates,
and everything else in the
[parameter reference](https://vee.navbytes.io/guide/plugin-authoring/#line-parameters).

This is a **reference, not a plugin** — nothing here is useful more than
once, which is exactly the bar [`../CONTRIBUTING.md`](../CONTRIBUTING.md)
holds the rest of this store to. That's why it lives outside the category
folders: `demo/` isn't in `scripts/build-catalog.py`'s `CATEGORIES`, so
Discover never lists it and CI's manifest checks never touch it.

[`controls.py`](controls.py) is also catalogued — as
[`../Showcase/controls.py`](../Showcase/controls.py), the one deliberate
exception to the "useful more than once" bar, so there's a single way to find
this from Discover. It's the same menu; come here for the TypeScript twin.

| File | Language |
| --- | --- |
| [`controls.py`](controls.py) | Python |
| [`controls.ts`](controls.ts) | TypeScript |

Both print the **exact same menu**, byte for byte — that's checked, not just
claimed:

```sh
diff <(python3 demo/controls.py) <(node demo/controls.ts)
diff <(python3 demo/controls.py) <(python3 Showcase/controls.py)
```

Every plugin in this store is a **dependency-free executable**: it builds
Vee's `key=value` text protocol (or the JSON output format — see
[the docs](https://vee.navbytes.io/guide/json-output/)) with plain
`print()`/`console.log()` calls and prints it to stdout, nothing imported.
This file is that in its most exhaustive form — read it alongside the
[parameter reference](https://vee.navbytes.io/guide/plugin-authoring/#line-parameters)
for what each `key=value` pair does, and note the two things you own by hand
that a typed builder would otherwise do for you:

1. **Quote any param value** containing whitespace, `|`, or `\` — see the
   `param1=`/`tooltip=` lines. Miss one and the value silently truncates at
   the first space Vee's parser hits.
2. **Escape a literal `|` or `\` inside display text** (not a param value) as
   `\|` / `\\`, or it's read as the text/params delimiter and corrupts the
   line — see "Reserved chars" in either file. This bit a hand-formatted
   plugin in this very repo once: `a|b` rendered as `a`, silently dropping
   `b and back\slash` as if it were parameters.

## Running one

```sh
vee dev ./demo/controls.py     # re-renders on every save
vee render ./demo/controls.ts  # one-shot render to the terminal
vee lint ./demo/controls.py    # 0 findings on both
```
