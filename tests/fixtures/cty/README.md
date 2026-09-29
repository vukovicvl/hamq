# cty.dat / cty.csv test excerpt

`cty_excerpt.dat` and `cty_excerpt.csv` are small excerpts of the AD1C country files. They
are used only by `tests/core/test_cty.py`.

| | |
|---|---|
| Source | "Big CTY" edition of the country files by Jim Reisert AD1C, <https://www.country-files.com/big-cty/> |
| Files | <https://www.country-files.com/bigcty/cty.dat>, <https://www.country-files.com/bigcty/cty.csv> |
| Release | 15 September 2026 (version marker `VER20260915`; the `VERSION` entity is Rotuma Island) |
| Downloaded | 2026-09-29; byte-identical to the files in `bigcty-20260915.zip` |

Thanks to AD1C for maintaining the country files.

## What was changed

- 21 of the 346 entities are kept, in file order: Montenegro, Vienna Intl Ctr (WAE),
  Croatia, Fed. Rep. of Germany, Bosnia-Herzegovina, Scotland, Shetland Islands (WAE),
  Italy, African Italy (WAE), Sicily (WAE), Japan, United States, Guantanamo Bay, Hawaii,
  Alaska, Austria, Asiatic Turkey, European Turkey (WAE), Canada, Australia, Serbia.
- Header lines are unchanged. Prefix lists are trimmed to a subset; every kept entry is
  unchanged (overrides included) and stays on its original line. In cty.csv the first nine
  columns are unchanged and the prefix column keeps the subset of its own entries.
- A `#` comment header was added to both files. The HamQ parser skips `#` lines.
- Line endings are CRLF, as in the originals.

To refresh the excerpt, download both files, keep the same entities and the entries the tests
use (for example `=AD1C(4)[7]`, `=KH6XX/0(4)[7]`, `=II0PN/MM(40)`, `VE3(4)[4]`, `W8(4)`, and
for the KG4 rule `KG4`, `=KG4AC`, `=W1AW/KG4` in Guantanamo Bay, `=KG4V(4)` in the United
States, `=KG4CAN` in Hawaii), update the release and download dates above and in the `#`
headers, and run the tests.

## Licence

The country files are distributed with this notice (`copyright.txt` in the release zip,
reproduced as published; the copyright line is incomplete in the original):

```text
Copyright © 1994-

Permission is hereby granted, free of charge, to any person obtaining a
copy of this software and associated documentation files (the
“Software”), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to
the following conditions:

The above copyright notice and this permission notice shall be included
in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS
OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

This excerpt is for tests only and must not be shipped with the plugin. HamQ never commits
or bundles the full files: they are downloaded at runtime and cached in the QGIS profile
folder, and `.gitignore` excludes `cty.dat` / `cty.csv` outside this folder.
