# Controls demo

Four files, one menu, every control the Vee plugin format supports —
`toggle=`, `slider=`, `progress=`, `pie=`/`donut=`/`stackedbar=`,
`sparkline=`, SF Symbols, Markdown, ANSI, images, submenus, alternates,
and everything else in the
[parameter reference](https://vee.navbytes.io/guide/plugin-authoring/#line-parameters).

This is a **reference, not a plugin** — nothing here is useful more than
once, which is exactly the bar [`../CONTRIBUTING.md`](../CONTRIBUTING.md)
holds the rest of this store to. That's why it lives outside the category
folders: `demo/` isn't in `scripts/build-catalog.py`'s `CATEGORIES`, so
Discover never lists it and CI's manifest checks never touch it.

One of the four, [`controls-sdk.py`](controls-sdk.py), is also catalogued —
as [`../Showcase/controls.py`](../Showcase/controls.py), the one deliberate
exception to the "useful more than once" bar, so there's a single way to find
this from Discover. It's the same menu; come here for the other three.

| File | SDK? | Language |
| --- | --- | --- |
| [`controls-sdk.py`](controls-sdk.py) | yes | Python |
| [`controls-raw.py`](controls-raw.py) | no — hand-formatted | Python |
| [`controls-sdk.ts`](controls-sdk.ts) | yes | TypeScript |
| [`controls-raw.ts`](controls-raw.ts) | no — hand-formatted | TypeScript |

All four print the **exact same menu**, byte for byte — that's checked, not
just claimed:

```sh
diff <(python3 demo/controls-sdk.py) <(python3 demo/controls-raw.py)
diff <(python3 demo/controls-sdk.py) <(node demo/controls-sdk.ts)
diff <(python3 demo/controls-sdk.py) <(node demo/controls-raw.ts)
```

Read the `-sdk` and `-raw` file for one language side by side. The SDK
version calls a typed builder (`d.item("Volume", slider={...})`); the raw
version prints the same `key=value | key=value` line by hand and comments on
every place a value needs manual quoting — which is the whole reason
[`../CONTRIBUTING.md`](../CONTRIBUTING.md#build-the-menu-with-the-sdk) tells
every other plugin here to use the SDK instead.

## Running one

```sh
vee dev ./demo/controls-sdk.py     # re-renders on every save
vee render ./demo/controls-sdk.ts  # one-shot render to the terminal
vee lint ./demo/controls-raw.py    # 0 findings on all four
```

The two `.py` files need `vee.py` beside them and the two `.ts` files need
`vee.ts` — both are vendored in this folder already (`demo/vee.py`,
`demo/vee.ts`), kept in sync with every other copy in the repo by
[`../scripts/sync-sdk.sh`](../scripts/sync-sdk.sh). `controls-raw.py` and
`controls-raw.ts` don't import them; they're included only because
`controls-sdk.*` need them to run.
