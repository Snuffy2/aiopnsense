# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/Snuffy2/aiopnsense/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                     |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|----------------------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| aiopnsense/\_\_init\_\_.py               |        4 |        0 |        0 |        0 |    100% |           |
| aiopnsense/\_dhcp\_leases.py             |      109 |        0 |       38 |        0 |    100% |           |
| aiopnsense/\_endpoint\_cache.py          |       31 |        0 |        6 |        0 |    100% |           |
| aiopnsense/\_endpoint\_outcome.py        |       43 |        0 |        2 |        0 |    100% |           |
| aiopnsense/\_error\_policy.py            |       70 |        0 |       18 |        0 |    100% |           |
| aiopnsense/\_firmware\_request\_guard.py |       18 |        0 |        4 |        0 |    100% |           |
| aiopnsense/\_request\_failure.py         |       41 |        0 |        8 |        0 |    100% |           |
| aiopnsense/\_sse\_decoder.py             |       66 |        0 |       12 |        0 |    100% |           |
| aiopnsense/\_styled\_endpoint.py         |        7 |        0 |        0 |        0 |    100% |           |
| aiopnsense/\_typing.py                   |        9 |        0 |        0 |        0 |    100% |           |
| aiopnsense/access.py                     |       17 |        0 |        2 |        0 |    100% |           |
| aiopnsense/client.py                     |       64 |        0 |       16 |        0 |    100% |           |
| aiopnsense/client\_base.py               |       67 |        0 |       14 |        0 |    100% |           |
| aiopnsense/client\_endpoint.py           |       72 |        0 |       24 |        0 |    100% |           |
| aiopnsense/client\_queue.py              |      130 |        7 |       42 |        4 |     92% |198-\>194, 200-201, 257-259, 286-\>293, 318, 328-\>324, 333 |
| aiopnsense/client\_toggle.py             |      190 |        1 |       62 |        1 |     99% |       390 |
| aiopnsense/client\_transport.py          |      201 |        0 |       74 |        1 |     99% | 408-\>384 |
| aiopnsense/const.py                      |       24 |        0 |        0 |        0 |    100% |           |
| aiopnsense/dhcp.py                       |      322 |        3 |      126 |        3 |     99% |566, 571, 739 |
| aiopnsense/exceptions.py                 |       45 |        0 |       22 |        1 |     99% | 109-\>111 |
| aiopnsense/firewall.py                   |      188 |        2 |       56 |        4 |     98% |62, 94-\>96, 96-\>98, 235 |
| aiopnsense/firmware.py                   |      313 |       13 |      142 |       14 |     94% |128, 238-\>256, 340-341, 381, 406, 481, 485, 550, 588, 622, 630-\>632, 655, 659-\>661, 665, 680-\>684, 685 |
| aiopnsense/helpers.py                    |      204 |        5 |       86 |        2 |     98% |400, 403-406 |
| aiopnsense/nut.py                        |       48 |        0 |       20 |        0 |    100% |           |
| aiopnsense/services.py                   |       69 |        1 |       20 |        1 |     98% |        78 |
| aiopnsense/smart.py                      |       38 |        0 |       12 |        0 |    100% |           |
| aiopnsense/speedtest.py                  |       54 |        0 |       14 |        0 |    100% |           |
| aiopnsense/system.py                     |      397 |        3 |      160 |        7 |     98% |303-\>305, 305-\>307, 308, 313-\>298, 339, 342, 708-\>699 |
| aiopnsense/telemetry.py                  |      251 |        3 |       70 |        3 |     98% |65, 117, 222 |
| aiopnsense/traffic.py                    |      112 |        0 |       52 |        1 |     99% | 151-\>154 |
| aiopnsense/unbound.py                    |       93 |        0 |       36 |        0 |    100% |           |
| aiopnsense/vnstat.py                     |      217 |        8 |       94 |        8 |     95% |220, 273, 389, 416, 459, 493, 513, 535 |
| aiopnsense/vouchers.py                   |       53 |        1 |       18 |        1 |     97% |        87 |
| aiopnsense/vpn.py                        |      563 |       11 |      266 |       14 |     97% |298, 322, 323-\>exit, 344, 393, 523-524, 593, 598, 635-\>591, 644, 823, 828-\>821, 1148, 1439-\>1435 |
| **TOTAL**                                | **4130** |   **58** | **1516** |   **65** | **98%** |           |


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