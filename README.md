# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/Snuffy2/aiopnsense/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                       |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|--------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| aiopnsense/\_\_init\_\_.py |        3 |        0 |        0 |        0 |    100% |           |
| aiopnsense/\_typing.py     |       35 |        0 |        0 |        0 |    100% |           |
| aiopnsense/client.py       |       13 |        0 |        0 |        0 |    100% |           |
| aiopnsense/client\_base.py |      346 |       18 |       98 |       20 |     91% |102->105, 113, 160->164, 222->262, 309-316, 382->412, 386->412, 390->412, 394->412, 410->412, 462->520, 491, 500, 517->520, 569, 586->589, 739->742, 817, 852, 887->889, 895-896, 903->900, 905-906 |
| aiopnsense/const.py        |        6 |        0 |        0 |        0 |    100% |           |
| aiopnsense/dhcp.py         |      207 |       54 |       98 |       32 |     69% |33-40, 58->60, 75, 109-120, 144, 146, 152, 153->150, 166, 184, 191-193, 217, 220, 227, 228->215, 253-254, 256-259, 264, 273, 288, 301-303, 327, 332, 333->336, 344, 362-363, 366-368, 394, 397, 398->401, 409, 423-431 |
| aiopnsense/exceptions.py   |        2 |        0 |        0 |        0 |    100% |           |
| aiopnsense/firewall.py     |      171 |       15 |       76 |       18 |     87% |85, 117, 120, 148, 151, 177, 180, 257->263, 275, 279, 329, 332, 336, 337->334, 346, 349->351, 368, 372 |
| aiopnsense/firmware.py     |       74 |       10 |       22 |        4 |     85% |26->34, 70, 92-100, 108-109, 115-116 |
| aiopnsense/helpers.py      |      100 |        6 |       34 |        2 |     94% |41, 212, 215-218 |
| aiopnsense/services.py     |       54 |        5 |       16 |        4 |     87% |26, 34-35, 57, 59->58, 63 |
| aiopnsense/speedtest.py    |       46 |        0 |       14 |        0 |    100% |           |
| aiopnsense/system.py       |      168 |       26 |       64 |       22 |     78% |46->63, 63->81, 70, 86-91, 113, 145, 162-173, 203, 210, 217, 219->223, 220->219, 224, 245, 254, 280, 298->297, 345, 346->343, 350-355, 380, 399, 402->401 |
| aiopnsense/telemetry.py    |      187 |       28 |       52 |       16 |     79% |29-38, 54, 59, 90->92, 172, 190, 218, 225-226, 238->247, 248->261, 251-252, 262-266, 272, 277, 305, 339, 360->359, 381, 384 |
| aiopnsense/unbound.py      |       45 |        0 |       16 |        0 |    100% |           |
| aiopnsense/vnstat.py       |      203 |       12 |       92 |       11 |     92% |201, 256, 340, 367-368, 378, 409, 411, 456, 499, 522, 549 |
| aiopnsense/vouchers.py     |       40 |        3 |       16 |        5 |     86% |34, 43, 48, 68->71, 72->76 |
| aiopnsense/vpn.py          |      183 |       12 |       92 |       21 |     88% |54-55, 89, 94->87, 117, 118->exit, 143, 146->141, 167, 170->172, 200, 230, 434, 460, 463->462, 465->458, 499->498, 513->512, 515->514, 580->exit, 625, 631 |
| **TOTAL**                  | **1883** |  **189** |  **690** |  **155** | **86%** |           |


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