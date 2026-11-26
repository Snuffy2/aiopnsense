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
| aiopnsense/\_wireguard.py                |      120 |        3 |       60 |        5 |     96% |159, 164-\>157, 194, 199, 236-\>192 |
| aiopnsense/access.py                     |       17 |        0 |        2 |        0 |    100% |           |
| aiopnsense/client.py                     |       64 |        0 |       16 |        0 |    100% |           |
| aiopnsense/client\_base.py               |       67 |        0 |       14 |        0 |    100% |           |
| aiopnsense/client\_endpoint.py           |       82 |        0 |       28 |        0 |    100% |           |
| aiopnsense/client\_queue.py              |      130 |        7 |       42 |        4 |     92% |198-\>194, 200-201, 257-259, 286-\>293, 318, 328-\>324, 333 |
| aiopnsense/client\_toggle.py             |      190 |        1 |       62 |        1 |     99% |       390 |
| aiopnsense/client\_transport.py          |      201 |        0 |       74 |        1 |     99% | 408-\>384 |
| aiopnsense/const.py                      |       24 |        0 |        0 |        0 |    100% |           |
| aiopnsense/dhcp.py                       |      357 |        1 |      138 |        1 |     99% |       849 |
| aiopnsense/exceptions.py                 |       45 |        0 |       22 |        1 |     99% | 109-\>111 |
| aiopnsense/firewall.py                   |      188 |        2 |       56 |        4 |     98% |62, 94-\>96, 96-\>98, 235 |
| aiopnsense/firmware.py                   |      315 |       12 |      144 |       13 |     95% |128, 238-\>256, 340-341, 381, 406, 481, 485, 550, 588, 622, 630-\>632, 655, 659-\>661, 685-\>689, 690 |
| aiopnsense/helpers.py                    |      204 |        5 |       86 |        2 |     98% |400, 403-406 |
| aiopnsense/nut.py                        |       49 |        0 |       20 |        0 |    100% |           |
| aiopnsense/services.py                   |       66 |        0 |       18 |        0 |    100% |           |
| aiopnsense/smart.py                      |       34 |        0 |       10 |        0 |    100% |           |
| aiopnsense/speedtest.py                  |       56 |        0 |       16 |        0 |    100% |           |
| aiopnsense/system.py                     |      403 |        3 |      162 |        7 |     98% |304-\>306, 306-\>308, 309, 314-\>299, 340, 343, 741-\>732 |
| aiopnsense/telemetry.py                  |      259 |        3 |       74 |        3 |     98% |65, 123, 228 |
| aiopnsense/traffic.py                    |      112 |        0 |       52 |        1 |     99% | 151-\>154 |
| aiopnsense/unbound.py                    |      105 |        1 |       42 |        1 |     99% |        40 |
| aiopnsense/vnstat.py                     |      223 |        7 |      100 |        7 |     96% |230, 283, 426, 469, 503, 523, 545 |
| aiopnsense/vouchers.py                   |       53 |        1 |       18 |        1 |     97% |        87 |
| aiopnsense/vpn.py                        |      436 |        7 |      210 |        8 |     98% |260, 284, 285-\>exit, 306, 355, 485-486, 741, 1032-\>1028 |
| **TOTAL**                                | **4198** |   **53** | **1554** |   **60** | **98%** |           |


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