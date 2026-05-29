# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/Snuffy2/aiopnsense/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                       |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|--------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| aiopnsense/\_\_init\_\_.py |        3 |        0 |        0 |        0 |    100% |           |
| aiopnsense/\_typing.py     |       33 |        0 |        0 |        0 |    100% |           |
| aiopnsense/client.py       |       57 |        1 |       10 |        0 |     99% |       137 |
| aiopnsense/client\_base.py |      334 |       17 |      112 |       18 |     92% |86, 189-\>192, 204, 291-298, 351-\>381, 355-\>381, 359-\>381, 363-\>381, 379-\>381, 424-\>482, 432-\>431, 462, 479-\>482, 522, 539-\>542, 659-\>662, 718, 798-\>800, 806-807, 814-\>811, 816-817 |
| aiopnsense/const.py        |       10 |        0 |        0 |        0 |    100% |           |
| aiopnsense/dhcp.py         |      235 |        4 |      116 |        9 |     96% |89-\>91, 106, 252, 265, 266-\>247, 295, 353-\>356, 416-\>419, 448-\>452 |
| aiopnsense/exceptions.py   |       10 |        0 |        0 |        0 |    100% |           |
| aiopnsense/firewall.py     |      186 |       10 |       84 |       13 |     91% |77, 113, 116, 142, 145, 171, 174, 236-\>242, 254, 258, 307, 308-\>305, 320-\>322 |
| aiopnsense/firmware.py     |       87 |       10 |       28 |        4 |     88% |50-\>52, 74, 96-104, 112-113, 119-120 |
| aiopnsense/helpers.py      |      114 |        6 |       44 |        2 |     95% |46, 195, 198-201 |
| aiopnsense/services.py     |       66 |        0 |       20 |        0 |    100% |           |
| aiopnsense/speedtest.py    |       51 |        0 |       16 |        0 |    100% |           |
| aiopnsense/system.py       |      330 |        3 |      146 |        7 |     98% |117-\>119, 119-\>121, 122, 127-\>112, 153, 156, 512-\>503 |
| aiopnsense/telemetry.py    |      221 |        0 |       64 |        0 |    100% |           |
| aiopnsense/unbound.py      |      140 |        5 |       56 |        1 |     97% |99-106, 136-141 |
| aiopnsense/vnstat.py       |      208 |       12 |       94 |       11 |     92% |192, 242, 306, 325-326, 336, 360, 362, 397, 429, 447, 468 |
| aiopnsense/vouchers.py     |       44 |        0 |       16 |        2 |     97% |71-\>74, 75-\>79 |
| aiopnsense/vpn.py          |      206 |        7 |      104 |       13 |     94% |130-\>exit, 200, 280-281, 420, 441, 444-\>443, 446-\>439, 475-\>474, 489-\>488, 491-\>490, 546-\>exit, 581, 587 |
| **TOTAL**                  | **2335** |   **75** |  **910** |   **80** | **95%** |           |


## Setup coverage badge

Below are examples of the badges you can use in your main branch `README` file.

### Direct image

[![Coverage badge](https://raw.githubusercontent.com/Snuffy2/aiopnsense/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/Snuffy2/aiopnsense/blob/python-coverage-comment-action-data/htmlcov/index.html)

This is the one to use if your repository is private or if you don't want to customize anything.

### [Shields.io](https://shields.io) Json Endpoint

[![Coverage badge](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/Snuffy2/aiopnsense/python-coverage-comment-action-data/endpoint.json)](https://htmlpreview.github.io/?https://github.com/Snuffy2/aiopnsense/blob/python-coverage-comment-action-data/htmlcov/index.html)

Using this one will allow you to [customize](https://shields.io/endpoint) the look of your badge.
It won't work with private repositories. It won't be refreshed more than once per five minutes.

### [Shields.io](https://shields.io) Dynamic Badge

[![Coverage badge](https://img.shields.io/badge/dynamic/json?color=brightgreen&label=coverage&query=%24.message&url=https%3A%2F%2Fraw.githubusercontent.com%2FSnuffy2%2Faiopnsense%2Fpython-coverage-comment-action-data%2Fendpoint.json)](https://htmlpreview.github.io/?https://github.com/Snuffy2/aiopnsense/blob/python-coverage-comment-action-data/htmlcov/index.html)

This one will always be the same color. It won't work for private repos. I'm not even sure why we included it.

## What is that?

This branch is part of the
[python-coverage-comment-action](https://github.com/marketplace/actions/python-coverage-comment)
GitHub Action. All the files in this branch are automatically generated and may be
overwritten at any moment.