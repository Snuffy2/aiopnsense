# Changelog

## [1.6.0](https://github.com/Snuffy2/aiopnsense/compare/v1.5.0...v1.6.0) (2026-11-26)


### Features

* add Unbound Query Stats and Query Log reads ([#243](https://github.com/Snuffy2/aiopnsense/issues/243)) ([22762f4](https://github.com/Snuffy2/aiopnsense/commit/22762f40af0750d3f6a01de9dcfea73768bf624f))
* report CARP access level ([#253](https://github.com/Snuffy2/aiopnsense/issues/253)) ([17af0fc](https://github.com/Snuffy2/aiopnsense/commit/17af0fcb048063c8c08d0415117a16a143a4e603))
* report DHCP lease access level ([#252](https://github.com/Snuffy2/aiopnsense/issues/252)) ([4b97653](https://github.com/Snuffy2/aiopnsense/commit/4b976530f487d337d241bab684d1b8a2278d2ac3))
* report VPN status and configuration access ([#245](https://github.com/Snuffy2/aiopnsense/issues/245)) ([6ac2aa9](https://github.com/Snuffy2/aiopnsense/commit/6ac2aa99b516847fe08fc4b5dc0e952ccf33d642))


### Bug Fixes

* distinguish failed Speedtest reads from a missing plugin ([#261](https://github.com/Snuffy2/aiopnsense/issues/261)) ([52e65cb](https://github.com/Snuffy2/aiopnsense/commit/52e65cbb86aa3efe5eab6191d94a4511beb2f55b))
* distinguish unavailable speedtest reads from missing plugin ([52e65cb](https://github.com/Snuffy2/aiopnsense/commit/52e65cbb86aa3efe5eab6191d94a4511beb2f55b))
* keep observing firmware install through an empty lock state ([#267](https://github.com/Snuffy2/aiopnsense/issues/267)) ([bf7cf6f](https://github.com/Snuffy2/aiopnsense/commit/bf7cf6fb6f72c2754fe83b44ea8058ff72176ba4))
* make shared-key WireGuard status order-independent ([#247](https://github.com/Snuffy2/aiopnsense/issues/247)) ([5e6597c](https://github.com/Snuffy2/aiopnsense/commit/5e6597c814dd5a5c4d30e0fc716805e230b5ca63))
* preserve NUT inventory when status reads fail ([#260](https://github.com/Snuffy2/aiopnsense/issues/260)) ([299709d](https://github.com/Snuffy2/aiopnsense/commit/299709d13df4a3340e360e5e8b41ea94392f86b8))
* preserve readable DHCP lease inventory ([#270](https://github.com/Snuffy2/aiopnsense/issues/270)) ([bfa524e](https://github.com/Snuffy2/aiopnsense/commit/bfa524ef44451c7a1f1ace7f928d75aabf73d38f))
* preserve Unbound switches when inventory reads fail ([#255](https://github.com/Snuffy2/aiopnsense/issues/255)) ([d1a59a1](https://github.com/Snuffy2/aiopnsense/commit/d1a59a15d7c5c26773bfd6387a93c687905244f5))
* preserve unknown gateway inventory on read failure ([#257](https://github.com/Snuffy2/aiopnsense/issues/257)) ([3764a05](https://github.com/Snuffy2/aiopnsense/commit/3764a05090fba1e36d80ae931aca8bf44218840f))
* preserve unknown interface inventories on read failure ([#254](https://github.com/Snuffy2/aiopnsense/issues/254)) ([8d2a152](https://github.com/Snuffy2/aiopnsense/commit/8d2a152c599c949e432e1a3973cc2c2a307ac370))
* preserve unknown service inventories ([#256](https://github.com/Snuffy2/aiopnsense/issues/256)) ([4dab499](https://github.com/Snuffy2/aiopnsense/commit/4dab49955bfb93d26a7b227b69e43fe7de153ba7))
* preserve unknown SMART results on read failure ([#259](https://github.com/Snuffy2/aiopnsense/issues/259)) ([cea1406](https://github.com/Snuffy2/aiopnsense/commit/cea1406126db8dad9a44a9cbd6e79f49b456b341))
* preserve unknown vnStat inventories on read failure ([#263](https://github.com/Snuffy2/aiopnsense/issues/263)) ([d38a6ce](https://github.com/Snuffy2/aiopnsense/commit/d38a6cee02adf2190abe4edc73028ec7a8c39546))
* preserve unreadable telemetry inventories ([#258](https://github.com/Snuffy2/aiopnsense/issues/258)) ([af11106](https://github.com/Snuffy2/aiopnsense/commit/af1110608489b48b7bb8e98e656e5814ee4f72db))
* preserve VPN status after configuration denial ([#246](https://github.com/Snuffy2/aiopnsense/issues/246)) ([b3d1565](https://github.com/Snuffy2/aiopnsense/commit/b3d1565e3b4b01955319991b28ffe4d1e54a2fa2))
* report an unreadable firewall rule group as unknown ([#251](https://github.com/Snuffy2/aiopnsense/issues/251)) ([d67dc1a](https://github.com/Snuffy2/aiopnsense/commit/d67dc1ae14eb59cff9f2205ddc351b8e939d8b21))
* report malformed service inventories as unknown ([#268](https://github.com/Snuffy2/aiopnsense/issues/268)) ([9c4be29](https://github.com/Snuffy2/aiopnsense/commit/9c4be293553a2a23a27add39123c22450be92c16))


### Documentation

* **agents:** allow read-only network lookups without asking ([#266](https://github.com/Snuffy2/aiopnsense/issues/266)) ([ec33a4c](https://github.com/Snuffy2/aiopnsense/commit/ec33a4cd8c7dc6d3acdf1eb64ce1c72069921f1c))
* align documentation with ADR 0004 ([#265](https://github.com/Snuffy2/aiopnsense/issues/265)) ([5f2d1b3](https://github.com/Snuffy2/aiopnsense/commit/5f2d1b3456e2348d208a86d4aafa3a4897dbbd8b))
* align glossary terms with hass-opnsense ([#250](https://github.com/Snuffy2/aiopnsense/issues/250)) ([d991331](https://github.com/Snuffy2/aiopnsense/commit/d991331bcf187c7336543050d8cd5ecd8ed90499))
* define Status Access and Configuration Access ([#244](https://github.com/Snuffy2/aiopnsense/issues/244)) ([2ec54ed](https://github.com/Snuffy2/aiopnsense/commit/2ec54edad36111a4c6c4e782b6c1f6b9e2b643e8))
* extend ADR 0004 to unknown inventories ([#262](https://github.com/Snuffy2/aiopnsense/issues/262)) ([97f8b72](https://github.com/Snuffy2/aiopnsense/commit/97f8b7227b47341f1ca77377654d560e635cddce))


### Code Refactoring

* centralize WireGuard link assembly ([#269](https://github.com/Snuffy2/aiopnsense/issues/269)) ([75e0a22](https://github.com/Snuffy2/aiopnsense/commit/75e0a22ee8d8f3bcc869b3ffd78ab08e76912f3e))
* share inventory read policy for Services and SMART ([#271](https://github.com/Snuffy2/aiopnsense/issues/271)) ([5cdf37b](https://github.com/Snuffy2/aiopnsense/commit/5cdf37b7a2a19ab8a64b418474c2d1475c693bce))


### Continuous Integration

* expose the checkout tested by pytest ([#264](https://github.com/Snuffy2/aiopnsense/issues/264)) ([496c603](https://github.com/Snuffy2/aiopnsense/commit/496c6035fa0553faa32bf81a3edbcb7a457f9440))

## [1.5.0](https://github.com/Snuffy2/aiopnsense/compare/v1.4.1...v1.5.0) (2026-11-24)


### Features

* expose firmware requirements via get_firmware_requirements ([#188](https://github.com/Snuffy2/aiopnsense/issues/188)) ([f9fb9a6](https://github.com/Snuffy2/aiopnsense/commit/f9fb9a65018bd1c44f4de8d5e481e096a5e4d549))


### Bug Fixes

* block forbidden firmware requests at the HTTP boundary ([#212](https://github.com/Snuffy2/aiopnsense/issues/212)) ([3205b7a](https://github.com/Snuffy2/aiopnsense/commit/3205b7a313542ae6bf59c166059b0f545321c862))
* close the client when validation fails in __aenter__ ([#190](https://github.com/Snuffy2/aiopnsense/issues/190)) ([ecdc6f3](https://github.com/Snuffy2/aiopnsense/commit/ecdc6f308f55f07363ee75b2067bf7bb9b0e0406))
* compare beta firmware builds and test real version shapes ([#209](https://github.com/Snuffy2/aiopnsense/issues/209)) ([334434c](https://github.com/Snuffy2/aiopnsense/commit/334434c04187c8f95e117a510dbd43500f6ea3f8))
* give firmware gating one policy for unknown firmware ([#195](https://github.com/Snuffy2/aiopnsense/issues/195)) ([91769ad](https://github.com/Snuffy2/aiopnsense/commit/91769adbaae2f5ee6fa5bba29475cb031879a2c5))
* guard legacy Unbound writes against incomplete settings ([#199](https://github.com/Snuffy2/aiopnsense/issues/199)) ([0925dbd](https://github.com/Snuffy2/aiopnsense/commit/0925dbdc2fc59d7d8ffb966251f95b1f717e62fa))
* keep validation’s error mode local ([#240](https://github.com/Snuffy2/aiopnsense/issues/240)) ([ebfb771](https://github.com/Snuffy2/aiopnsense/commit/ebfb7715ea5ec130daf9ec9d53d03576c717804b))
* limit automatic firmware checks to one per hour ([#205](https://github.com/Snuffy2/aiopnsense/issues/205)) ([e5f92d5](https://github.com/Snuffy2/aiopnsense/commit/e5f92d5855dad48ae2cf86c6b016c6f687268295))
* name public methods in request failure logs and give dispatch one home ([#227](https://github.com/Snuffy2/aiopnsense/issues/227)) ([9eac267](https://github.com/Snuffy2/aiopnsense/commit/9eac2678b72a849d80fd271a27c87000e678e427))
* raise Unbound blocklist errors when throw_errors is on ([#194](https://github.com/Snuffy2/aiopnsense/issues/194)) ([1cb9b51](https://github.com/Snuffy2/aiopnsense/commit/1cb9b51f792be329f7dc0d3575a683e1687481a5))
* read the system time endpoint of the firmware's Endpoint Style ([#238](https://github.com/Snuffy2/aiopnsense/issues/238)) ([360e01a](https://github.com/Snuffy2/aiopnsense/commit/360e01a7f8af0a7826fcb1c54a14d07d97eb1ff3))
* report every HTTP request failure through one shared classification ([#196](https://github.com/Snuffy2/aiopnsense/issues/196)) ([c26612f](https://github.com/Snuffy2/aiopnsense/commit/c26612f77cd2e965d7756ffcac721a6cc76d4ef6))
* reset endpoint state after verified firmware install ([#242](https://github.com/Snuffy2/aiopnsense/issues/242)) ([a1c46de](https://github.com/Snuffy2/aiopnsense/commit/a1c46de9ac3167992906d45360e561c9b41c0b7a))
* run every Toggle through one Toggle module and stop reporting unsaved changes as success ([#197](https://github.com/Snuffy2/aiopnsense/issues/197)) ([a4226f3](https://github.com/Snuffy2/aiopnsense/commit/a4226f3105c4f4ed3b8b5d7676f459be29f2eac1))
* skip queued POSTs cancelled before dispatch ([#200](https://github.com/Snuffy2/aiopnsense/issues/200)) ([36cb432](https://github.com/Snuffy2/aiopnsense/commit/36cb43266f388fd6ed503d194c62363e7cce5c47))
* support explicit VPN toggle target states ([#185](https://github.com/Snuffy2/aiopnsense/issues/185)) ([e52757c](https://github.com/Snuffy2/aiopnsense/commit/e52757cf22dcb29ad70a6caa655cbc394b191d11))
* treat empty WireGuard inventory lists as empty ([#229](https://github.com/Snuffy2/aiopnsense/issues/229)) ([85bdb4c](https://github.com/Snuffy2/aiopnsense/commit/85bdb4cce80d6387037f21eeec4a23a37d14f221))
* treat trailing zero version components as equal in firmware_is_at_least ([#189](https://github.com/Snuffy2/aiopnsense/issues/189)) ([13e7244](https://github.com/Snuffy2/aiopnsense/commit/13e7244efe947447d7834e2eaef965554ec97bcb))


### Dependencies

* bump astral-sh/setup-uv from 10.3.2 to 10.3.3 in the github-actions group ([#177](https://github.com/Snuffy2/aiopnsense/issues/177)) ([2f33a4e](https://github.com/Snuffy2/aiopnsense/commit/2f33a4eba5364a2c47bb9a223cab7bcbce36eb3e))
* bump py-cov-action/python-coverage-comment-action from 4.5 to 4.6 in the github-actions group ([#183](https://github.com/Snuffy2/aiopnsense/issues/183)) ([ceb59d0](https://github.com/Snuffy2/aiopnsense/commit/ceb59d0f61b1a14477e8f5d265454a5efb1d8a1a))
* bump the python-dependencies group with 10 updates ([#178](https://github.com/Snuffy2/aiopnsense/issues/178)) ([048cab5](https://github.com/Snuffy2/aiopnsense/commit/048cab577bd9dc5cc2b26a51b840e9ef1df8fe01))
* bump the python-dependencies group with 10 updates ([#181](https://github.com/Snuffy2/aiopnsense/issues/181)) ([ac34f6c](https://github.com/Snuffy2/aiopnsense/commit/ac34f6ce4e2f16113c87f6e0e9edd09194650b15))
* bump the python-dependencies group with 6 updates ([#184](https://github.com/Snuffy2/aiopnsense/issues/184)) ([dd33af2](https://github.com/Snuffy2/aiopnsense/commit/dd33af24ce15e765bdfd0eb96e56182d9aad6bda))
* update prek hooks ([#179](https://github.com/Snuffy2/aiopnsense/issues/179)) ([f091275](https://github.com/Snuffy2/aiopnsense/commit/f09127559e0caa61fafa0ee566fa814c0ad87b63))
* update prek hooks ([#182](https://github.com/Snuffy2/aiopnsense/issues/182)) ([f39ff84](https://github.com/Snuffy2/aiopnsense/commit/f39ff84a14ba9bde248fdbb9b08d5fd0a7db024b))
* update prek hooks ([#226](https://github.com/Snuffy2/aiopnsense/issues/226)) ([64a6b40](https://github.com/Snuffy2/aiopnsense/commit/64a6b40278e0ace71213a72de42da5b858760fcb))


### Documentation

* document worktree imports and test conventions for agents ([#192](https://github.com/Snuffy2/aiopnsense/issues/192)) ([6d8cb6c](https://github.com/Snuffy2/aiopnsense/commit/6d8cb6c2bac6ca8ead3f9324f5fa5d5aff4bc163))
* note that pydoclint runs through prek ([#211](https://github.com/Snuffy2/aiopnsense/issues/211)) ([ab7c9dc](https://github.com/Snuffy2/aiopnsense/commit/ab7c9dcd898ec0c76e7ffd9addca047840990be9))
* record read-only tests and endpoint tracing ([#223](https://github.com/Snuffy2/aiopnsense/issues/223)) ([3a35a3b](https://github.com/Snuffy2/aiopnsense/commit/3a35a3bc43d785e46794b71e27b9ff9984f4fd36))
* require tracing Router failure paths before trusting responses ([#207](https://github.com/Snuffy2/aiopnsense/issues/207)) ([d61bb11](https://github.com/Snuffy2/aiopnsense/commit/d61bb111f9615853caa946ec42b6c42e0affa347))
* say how to find the main checkout venv ([#230](https://github.com/Snuffy2/aiopnsense/issues/230)) ([919b4d5](https://github.com/Snuffy2/aiopnsense/commit/919b4d5517d376c46435ceb84007787ed902664b))
* show how to run live Router scripts from worktrees ([#206](https://github.com/Snuffy2/aiopnsense/issues/206)) ([0a6aec1](https://github.com/Snuffy2/aiopnsense/commit/0a6aec1a1596491f8d25c3e072a957bc45fa29d1))
* specify public behavior in refactor specs ([#203](https://github.com/Snuffy2/aiopnsense/issues/203)) ([badd29b](https://github.com/Snuffy2/aiopnsense/commit/badd29b9c7321d3fa8be70b0771ef7ed01ff40b5))


### Miscellaneous Chores

* add domain glossary, coding standards, Toggle ADR, and rename recommended firmware constant ([#187](https://github.com/Snuffy2/aiopnsense/issues/187)) ([fe1fba2](https://github.com/Snuffy2/aiopnsense/commit/fe1fba25407e57bc07720e55aa1b26c959c9f6ed))
* document API placement and add a manual docs build hook ([#191](https://github.com/Snuffy2/aiopnsense/issues/191)) ([4b88e41](https://github.com/Snuffy2/aiopnsense/commit/4b88e416b2cfcc86004af415df1ec1a2c7dacce2))
* set up mattpocock agent skills config ([#186](https://github.com/Snuffy2/aiopnsense/issues/186)) ([4a261f2](https://github.com/Snuffy2/aiopnsense/commit/4a261f285e10afeeafe678f69e74bee8072aa08c))


### Code Refactoring

* add endpoint outcome reads beside the probe path ([#213](https://github.com/Snuffy2/aiopnsense/issues/213)) ([14df796](https://github.com/Snuffy2/aiopnsense/commit/14df796407310d8b828684cda8545906be14f126))
* decode server-sent events in one pure module ([#235](https://github.com/Snuffy2/aiopnsense/issues/235)) ([c2d2892](https://github.com/Snuffy2/aiopnsense/commit/c2d289245db1f930405487111b55b3c9233b72cc))
* give the endpoint availability cache one home ([#234](https://github.com/Snuffy2/aiopnsense/issues/234)) ([e9b2b9a](https://github.com/Snuffy2/aiopnsense/commit/e9b2b9adf5f7af340c2cb2ed51bcc451e46e4060))
* give the transport one request body ([#236](https://github.com/Snuffy2/aiopnsense/issues/236)) ([adb02c8](https://github.com/Snuffy2/aiopnsense/commit/adb02c86d4c83b2e1862e958833910d54d098479))
* match each WireGuard link once ([#239](https://github.com/Snuffy2/aiopnsense/issues/239)) ([6bb6565](https://github.com/Snuffy2/aiopnsense/commit/6bb65657435c4e96406c6b09f004f92036c420f8))
* read DHCP and SMART endpoints in one request ([#215](https://github.com/Snuffy2/aiopnsense/issues/215)) ([0fd5dc0](https://github.com/Snuffy2/aiopnsense/commit/0fd5dc01efd918bb1315ae458780fbe1ab8ec3ea))
* read firewall, unbound, vouchers, vnstat, traffic and speedtest in one request ([#217](https://github.com/Snuffy2/aiopnsense/issues/217)) ([abd84d0](https://github.com/Snuffy2/aiopnsense/commit/abd84d06ba7b8a5384b4f9290eeec2e1b5cb820d))
* read firmware status in one request ([#218](https://github.com/Snuffy2/aiopnsense/issues/218)) ([18f30d6](https://github.com/Snuffy2/aiopnsense/commit/18f30d6c4a929463575005874a416732944851bc))
* read telemetry and system endpoints in one request ([#216](https://github.com/Snuffy2/aiopnsense/issues/216)) ([b3ed5b9](https://github.com/Snuffy2/aiopnsense/commit/b3ed5b96c1fe212cdab83d6dbc3fdabb63221c4d))
* read VPN inventories in one request ([#214](https://github.com/Snuffy2/aiopnsense/issues/214)) ([e4209b1](https://github.com/Snuffy2/aiopnsense/commit/e4209b1fdccbb70056a1cb5a602cfe3b867158f2))
* remove the legacy endpoint probe path ([#219](https://github.com/Snuffy2/aiopnsense/issues/219)) ([aa82631](https://github.com/Snuffy2/aiopnsense/commit/aa82631ccf71b6facb5500378de50eb6d7ecacd0))
* resolve Endpoint Style inside the fetch ([#237](https://github.com/Snuffy2/aiopnsense/issues/237)) ([6491bd2](https://github.com/Snuffy2/aiopnsense/commit/6491bd2c59675e60ded6ebd45cf2c12189c57c48))
* share DHCP Lease normalization across backends ([#193](https://github.com/Snuffy2/aiopnsense/issues/193)) ([933c40b](https://github.com/Snuffy2/aiopnsense/commit/933c40b1b616e9ebe6375782b7739994987aa651))


### Tests

* count only validation error logs ([#241](https://github.com/Snuffy2/aiopnsense/issues/241)) ([7df252f](https://github.com/Snuffy2/aiopnsense/commit/7df252fd6ccc82676da322b2830ac3baaf17e472))
* cover queued failures from the PR [#194](https://github.com/Snuffy2/aiopnsense/issues/194) retrospective ([#201](https://github.com/Snuffy2/aiopnsense/issues/201)) ([9ec3ae3](https://github.com/Snuffy2/aiopnsense/commit/9ec3ae38efa61047091d161fdd27e7c0b6cdb3be))
* enforce private names in private modules ([#202](https://github.com/Snuffy2/aiopnsense/issues/202)) ([a385763](https://github.com/Snuffy2/aiopnsense/commit/a38576323fa2eec272d3cf0704458c21f287fde5))
* enforce the firmware endpoint rule from ADR 0002 ([#208](https://github.com/Snuffy2/aiopnsense/issues/208)) ([5bec4d4](https://github.com/Snuffy2/aiopnsense/commit/5bec4d4b62f55e686ce1b5f2afdd66763345a6f1))
* fail fast on unanswered queued requests ([#222](https://github.com/Snuffy2/aiopnsense/issues/222)) ([8ca65c2](https://github.com/Snuffy2/aiopnsense/commit/8ca65c25a5d3dba76720fda26e1d69f254738261))
* guard request logs against URL credential leaks ([#204](https://github.com/Snuffy2/aiopnsense/issues/204)) ([0837c63](https://github.com/Snuffy2/aiopnsense/commit/0837c6343115180fdd545eb1fbd9885234ff9849))
* make the aiohttp session the one test seam ([#228](https://github.com/Snuffy2/aiopnsense/issues/228)) ([bd8a460](https://github.com/Snuffy2/aiopnsense/commit/bd8a4609c2c1255adba035445e34e39ae7d41871))
* pin endpoint paths as literals in every test module ([#233](https://github.com/Snuffy2/aiopnsense/issues/233)) ([e9b8f38](https://github.com/Snuffy2/aiopnsense/commit/e9b8f38114f2cd854f9df3bbe93e2de52ac523b6))
* pin firmware endpoint paths as literals ([#232](https://github.com/Snuffy2/aiopnsense/issues/232)) ([cfe686f](https://github.com/Snuffy2/aiopnsense/commit/cfe686f522e0a3a94aa3e1283ba8e22a837e1812))


### Continuous Integration

* run pytest under a second timezone ([#231](https://github.com/Snuffy2/aiopnsense/issues/231)) ([ffc2ffa](https://github.com/Snuffy2/aiopnsense/commit/ffc2ffa650c7162ca487a67687fa3eee62c22891))

## [1.4.1](https://github.com/Snuffy2/aiopnsense/compare/v1.4.0...v1.4.1) (2026-11-06)


### Bug Fixes

* POST to unbound/service/dnsbl so blocklist toggles apply on OPNsense &gt;= 26.1.6 ([#176](https://github.com/Snuffy2/aiopnsense/issues/176)) ([b6d98ef](https://github.com/Snuffy2/aiopnsense/commit/b6d98ef5eaec866af17f4266018f2b62995e3f76))
* POST to unbound/service/dnsbl so extended blocklist toggles apply ([b6d98ef](https://github.com/Snuffy2/aiopnsense/commit/b6d98ef5eaec866af17f4266018f2b62995e3f76))


### Dependencies

* update prek hooks ([#174](https://github.com/Snuffy2/aiopnsense/issues/174)) ([a28688b](https://github.com/Snuffy2/aiopnsense/commit/a28688b5d92627cb9966f31063e7a80aabcc7039))

## [1.4.0](https://github.com/Snuffy2/aiopnsense/compare/v1.3.3...v1.4.0) (2026-11-01)


### Features

* return normalized ARP and NDP table entries ([#172](https://github.com/Snuffy2/aiopnsense/issues/172)) ([3538339](https://github.com/Snuffy2/aiopnsense/commit/3538339f0e4a9e960005af0b31a66eb5a30b424e))


### Dependencies

* bump astral-sh/setup-uv from 10.3.0 to 10.3.2 in the github-actions group ([#170](https://github.com/Snuffy2/aiopnsense/issues/170)) ([8484c75](https://github.com/Snuffy2/aiopnsense/commit/8484c75abb009819e45fb6ea152a864c9020addc))
* bump the python-dependencies group with 6 updates ([#171](https://github.com/Snuffy2/aiopnsense/issues/171)) ([3dc27f8](https://github.com/Snuffy2/aiopnsense/commit/3dc27f8d2c5c595239dfe68c8ad812c0ec2ae48c))

## [1.3.3](https://github.com/Snuffy2/aiopnsense/compare/v1.3.2...v1.3.3) (2026-10-30)


### Bug Fixes

* **deps:** update certifi to remove compromised CA trust ([#165](https://github.com/Snuffy2/aiopnsense/issues/165)) ([6b37b1d](https://github.com/Snuffy2/aiopnsense/commit/6b37b1db107b2d96cb1f54c492ecab99c91c49e8))
* migrate pydoclint configuration for updated hooks ([#167](https://github.com/Snuffy2/aiopnsense/issues/167)) ([8c909a9](https://github.com/Snuffy2/aiopnsense/commit/8c909a9a65c28ee84a73a66fc15b0267fb433e1b))
* verify firmware installation lifecycle ([#169](https://github.com/Snuffy2/aiopnsense/issues/169)) ([cd9884d](https://github.com/Snuffy2/aiopnsense/commit/cd9884d85b5901916337d1e1575229e22856722d))


### Dependencies

* bump the python-dependencies group with 3 updates ([#164](https://github.com/Snuffy2/aiopnsense/issues/164)) ([16c1c0c](https://github.com/Snuffy2/aiopnsense/commit/16c1c0c4491f146aa95ad580c4db483b7a4f0e4e))
* bump uv from 0.13.0 to 0.13.4 ([#163](https://github.com/Snuffy2/aiopnsense/issues/163)) ([b2ae207](https://github.com/Snuffy2/aiopnsense/commit/b2ae2078f10d821201904d491bac060ed7d8eab7))
* update prek hooks ([#162](https://github.com/Snuffy2/aiopnsense/issues/162)) ([84de3d2](https://github.com/Snuffy2/aiopnsense/commit/84de3d25ee2cf57118efd2667005c7d24cd660f4))
* update prek hooks ([#168](https://github.com/Snuffy2/aiopnsense/issues/168)) ([3029184](https://github.com/Snuffy2/aiopnsense/commit/30291840effecdbb6f48c289bf5a0741896f8346))

## [1.3.2](https://github.com/Snuffy2/aiopnsense/compare/v1.3.1...v1.3.2) (2026-10-18)


### Bug Fixes

* reject incomplete IPsec, OpenVPN, and WireGuard inventories ([#160](https://github.com/Snuffy2/aiopnsense/issues/160)) ([5b7bb1d](https://github.com/Snuffy2/aiopnsense/commit/5b7bb1d674d0b6300d65e9c8045ff81201a3767e))

## [1.3.1](https://github.com/Snuffy2/aiopnsense/compare/v1.3.0...v1.3.1) (2026-10-17)


### Features

* add IPsec child (phase 2) status to get_ipsec ([#153](https://github.com/Snuffy2/aiopnsense/issues/153)) ([f1df285](https://github.com/Snuffy2/aiopnsense/commit/f1df285dcdbb14df0f3b89c517699f3afe7f87fd))
* add IPsec connection enabled state and toggling ([#151](https://github.com/Snuffy2/aiopnsense/issues/151)) ([bba8894](https://github.com/Snuffy2/aiopnsense/commit/bba8894b52a6ee22b2ea05696f7f25c5f05871a3))


### Bug Fixes

* distinguish unavailable VPN status from empty sessions ([#159](https://github.com/Snuffy2/aiopnsense/issues/159)) ([87beec3](https://github.com/Snuffy2/aiopnsense/commit/87beec3ed03be6f0be428c0bb0050a37e49e745c))
* suppress repeated request outage logs until recovery ([#152](https://github.com/Snuffy2/aiopnsense/issues/152)) ([a16d90f](https://github.com/Snuffy2/aiopnsense/commit/a16d90fa539dbac48e2c64acfa2c907c48237387))


### Dependencies

* bump astral-sh/setup-uv from 10.2.0 to 10.3.0 in the github-actions group ([#157](https://github.com/Snuffy2/aiopnsense/issues/157)) ([bd59948](https://github.com/Snuffy2/aiopnsense/commit/bd59948baed5d188bb570f1fed7a7cb0f3cd1ee5))
* bump the python-dependencies group with 5 updates ([#158](https://github.com/Snuffy2/aiopnsense/issues/158)) ([bb2de9d](https://github.com/Snuffy2/aiopnsense/commit/bb2de9d32832c19783ab3b377380f62d4632760f))


### Miscellaneous Chores

* add Snuffy2 as code owner ([#156](https://github.com/Snuffy2/aiopnsense/issues/156)) ([e952c6e](https://github.com/Snuffy2/aiopnsense/commit/e952c6eca4af098a506e70612d4af4fc1a2fbf87))


### Tests

* avoid timezone overflow in voucher fixtures ([#154](https://github.com/Snuffy2/aiopnsense/issues/154)) ([dd602e8](https://github.com/Snuffy2/aiopnsense/commit/dd602e847b09a3192b016a303dea94290adac534))

## [1.3.0](https://github.com/Snuffy2/aiopnsense/compare/v1.2.1...v1.3.0) (2026-10-16)


### Features

* add IPsec phase 1 connection status via get_ipsec ([#149](https://github.com/Snuffy2/aiopnsense/issues/149)) ([97902cd](https://github.com/Snuffy2/aiopnsense/commit/97902cd93fd8d7a3e8d6e228f982bfc3b5eb084d))
* expose timestamped MAC host observations ([#148](https://github.com/Snuffy2/aiopnsense/issues/148)) ([0480523](https://github.com/Snuffy2/aiopnsense/commit/0480523e0016813808744688fb931c066aaa9c59))

## [1.2.1](https://github.com/Snuffy2/aiopnsense/compare/v1.2.0...v1.2.1) (2026-10-12)


### Bug Fixes

* correct firmware hotfix version ordering ([#146](https://github.com/Snuffy2/aiopnsense/issues/146)) ([82fd517](https://github.com/Snuffy2/aiopnsense/commit/82fd517f9709f3d158784e62ff0fc0eece98b6ce))

## [1.2.0](https://github.com/Snuffy2/aiopnsense/compare/v1.1.11...v1.2.0) (2026-10-09)


### Features

* add interface toggling API ([#143](https://github.com/Snuffy2/aiopnsense/issues/143)) ([9792320](https://github.com/Snuffy2/aiopnsense/commit/979232052d9f582a8c69307edbf8b1409dc64334))
* add IPv6 NDP neighbor table support ([#144](https://github.com/Snuffy2/aiopnsense/issues/144)) ([4ab17a1](https://github.com/Snuffy2/aiopnsense/commit/4ab17a1f1092b62ed68c909936a47017e4b6aa21))


### Bug Fixes

* group Dependabot GitHub Actions updates ([#140](https://github.com/Snuffy2/aiopnsense/issues/140)) ([b28f2b6](https://github.com/Snuffy2/aiopnsense/commit/b28f2b6eeb5e406ebbe5ab61e01f761a8235d22a))


### Dependencies

* bump astral-sh/setup-uv from 10.0.1 to 10.1.0 ([#134](https://github.com/Snuffy2/aiopnsense/issues/134)) ([19a184b](https://github.com/Snuffy2/aiopnsense/commit/19a184b1e65826d895c83094417ffd04a53e52d1))
* bump astral-sh/setup-uv from 10.1.0 to 10.2.0 ([#138](https://github.com/Snuffy2/aiopnsense/issues/138)) ([0f5ce2c](https://github.com/Snuffy2/aiopnsense/commit/0f5ce2c38dd253adb8e6ef1cb7ca0f16ce9f08b4))
* bump py-cov-action/python-coverage-comment-action from 4.3 to 4.5 ([#137](https://github.com/Snuffy2/aiopnsense/issues/137)) ([0092a0f](https://github.com/Snuffy2/aiopnsense/commit/0092a0fd77e3ebaf177404e21f3fd94b55aa6b6f))
* bump the python-dependencies group with 4 updates ([#135](https://github.com/Snuffy2/aiopnsense/issues/135)) ([310fbe9](https://github.com/Snuffy2/aiopnsense/commit/310fbe9ec034cedc5faae79407daeeb0194ee1cd))
* bump the python-dependencies group with 4 updates ([#136](https://github.com/Snuffy2/aiopnsense/issues/136)) ([853b217](https://github.com/Snuffy2/aiopnsense/commit/853b217aea55903a318f75ce9156e7571d07a7da))
* bump the python-dependencies group with 4 updates ([#139](https://github.com/Snuffy2/aiopnsense/issues/139)) ([c4ce98b](https://github.com/Snuffy2/aiopnsense/commit/c4ce98b079fc7960b739119a457ad6b28cd4fce0))
* bump the python-dependencies group with 4 updates ([#142](https://github.com/Snuffy2/aiopnsense/issues/142)) ([ec4e056](https://github.com/Snuffy2/aiopnsense/commit/ec4e0565404e9495daff3ebb6e75454747843061))


### Miscellaneous Chores

* **deps:** patch vulnerable multidict and urllib3 ([#145](https://github.com/Snuffy2/aiopnsense/issues/145)) ([0195d41](https://github.com/Snuffy2/aiopnsense/commit/0195d41661efca7701af8ecfb7fbb49c26b07c1e))

## [1.1.11](https://github.com/Snuffy2/aiopnsense/compare/v1.1.10...v1.1.11) (2026-09-13)


### Bug Fixes

* accept release commit SHA as publish target ([#131](https://github.com/Snuffy2/aiopnsense/issues/131)) ([869f88f](https://github.com/Snuffy2/aiopnsense/commit/869f88f4d2db85f9072f593451a13fea60777ad8))
* distinguish failed ARP lookups from empty tables ([#133](https://github.com/Snuffy2/aiopnsense/issues/133)) ([8c1b5c1](https://github.com/Snuffy2/aiopnsense/commit/8c1b5c11633ce687ea193f01862da4994dae6e42))

## [1.1.10](https://github.com/Snuffy2/aiopnsense/compare/v1.1.9...v1.1.10) (2026-09-13)


### Bug Fixes

* include all Release Please changelog types ([#128](https://github.com/Snuffy2/aiopnsense/issues/128)) ([3317a8b](https://github.com/Snuffy2/aiopnsense/commit/3317a8b5ce71ee2f4ac4a3625118cc1796198125))
* instance service control paths ([#111](https://github.com/Snuffy2/aiopnsense/issues/111)) ([daa6feb](https://github.com/Snuffy2/aiopnsense/commit/daa6febe6c672884b248e1e632328184768b7a13))
* recognize releasable dependency updates ([#126](https://github.com/Snuffy2/aiopnsense/issues/126)) ([a5e4a22](https://github.com/Snuffy2/aiopnsense/commit/a5e4a2232ae099002ac3988e0d34699b2250ed02))


### Dependencies

* bump actions/download-artifact from 7 to 8 ([fc2e871](https://github.com/Snuffy2/aiopnsense/commit/fc2e871f3b16147a67c6772b4a5d023d0fc57f47))
* bump the python-dependencies group with 3 updates ([#130](https://github.com/Snuffy2/aiopnsense/issues/130)) ([b86a46c](https://github.com/Snuffy2/aiopnsense/commit/b86a46cc640517f43e2abb2ba2de5e8b4f2c3add))


### Miscellaneous Chores

* **deps:** bump the python-dependencies group with 2 updates ([#125](https://github.com/Snuffy2/aiopnsense/issues/125)) ([433148a](https://github.com/Snuffy2/aiopnsense/commit/433148a453c56dd08fe47b800c7726f89ee8a40b))
* update prek hooks ([#118](https://github.com/Snuffy2/aiopnsense/issues/118)) ([73a9985](https://github.com/Snuffy2/aiopnsense/commit/73a99859325c876b2de53ad020c4f012bee764e8))


### Continuous Integration

* bump Release Please action to v5 ([#122](https://github.com/Snuffy2/aiopnsense/issues/122)) ([6e629ba](https://github.com/Snuffy2/aiopnsense/commit/6e629ba9cd24afe1b061bc9d0a80494a832c4713))
* enforce semantic pull request titles ([#123](https://github.com/Snuffy2/aiopnsense/issues/123)) ([4c4285e](https://github.com/Snuffy2/aiopnsense/commit/4c4285e23b887fb0e8bcbcf55d3bd624623432ee))
* harden release workflow execution ([#129](https://github.com/Snuffy2/aiopnsense/issues/129)) ([2bcd02f](https://github.com/Snuffy2/aiopnsense/commit/2bcd02f116b6ce92c173b4804345a451972b7aaf))

## [v1.1.9](https://github.com/Snuffy2/aiopnsense/tree/v1.1.9) (2026-09-04)

[Full Changelog](https://github.com/Snuffy2/aiopnsense/compare/v1.1.8...v1.1.9)

**Bug Fixes**

- Fix empty Dnsmasq lease interface discovery [\#108](https://github.com/Snuffy2/aiopnsense/pull/108) ([Snuffy2](https://github.com/Snuffy2))

**Code Quality**

- Add OpenVPN toggle response logging [\#113](https://github.com/Snuffy2/aiopnsense/pull/113) ([Snuffy2](https://github.com/Snuffy2))
- Modernize CI and release automation with uv [\#106](https://github.com/Snuffy2/aiopnsense/pull/106) ([Snuffy2](https://github.com/Snuffy2))

**Maintenance**

- Fix protected release promotion statuses [\#114](https://github.com/Snuffy2/aiopnsense/pull/114) ([Snuffy2](https://github.com/Snuffy2))
- Migrate release workflow to published releases [\#112](https://github.com/Snuffy2/aiopnsense/pull/112) ([Snuffy2](https://github.com/Snuffy2))
- Fix GitHub release repository detection [\#110](https://github.com/Snuffy2/aiopnsense/pull/110) ([Snuffy2](https://github.com/Snuffy2))
- Update pytest coverage workflows for action v4 [\#109](https://github.com/Snuffy2/aiopnsense/pull/109) ([Snuffy2](https://github.com/Snuffy2))
- Skip coverage comments for prek autoupdate bot [\#104](https://github.com/Snuffy2/aiopnsense/pull/104) ([Snuffy2](https://github.com/Snuffy2))
- Add prek-autoupdate bot auto-merge workflow [\#102](https://github.com/Snuffy2/aiopnsense/pull/102) ([Snuffy2](https://github.com/Snuffy2))

**Other Changes**

- test: optimize and de-brittle test suite [\#100](https://github.com/Snuffy2/aiopnsense/pull/100) ([Snuffy2](https://github.com/Snuffy2))

## [v1.1.8](https://github.com/Snuffy2/aiopnsense/tree/v1.1.8) (2026-08-30)

[Full Changelog](https://github.com/Snuffy2/aiopnsense/compare/v1.1.7...v1.1.8)

## [v1.1.7](https://github.com/Snuffy2/aiopnsense/tree/v1.1.7) (2026-08-08)

[Full Changelog](https://github.com/Snuffy2/aiopnsense/compare/v1.1.6...v1.1.7)

**Documentation**

- Add pydoclint documentation checks [\#95](https://github.com/Snuffy2/aiopnsense/pull/95) ([Snuffy2](https://github.com/Snuffy2))

**Code Quality**

- Reuse parsed vnStat period labels [\#99](https://github.com/Snuffy2/aiopnsense/pull/99) ([Snuffy2](https://github.com/Snuffy2))
- Optimize and harden DHCP lease collection [\#98](https://github.com/Snuffy2/aiopnsense/pull/98) ([Snuffy2](https://github.com/Snuffy2))
- Defer caught-error log rendering [\#97](https://github.com/Snuffy2/aiopnsense/pull/97) ([Snuffy2](https://github.com/Snuffy2))
- Avoid expensive caller stack inspection [\#96](https://github.com/Snuffy2/aiopnsense/pull/96) ([Snuffy2](https://github.com/Snuffy2))

## [v1.1.6](https://github.com/Snuffy2/aiopnsense/tree/v1.1.6) (2026-08-01)

[Full Changelog](https://github.com/Snuffy2/aiopnsense/compare/v1.1.5...v1.1.6)

**Bug Fixes**

- Cache endpoint permission failures [\#91](https://github.com/Snuffy2/aiopnsense/pull/91) ([Snuffy2](https://github.com/Snuffy2))

**Maintenance**

- Update GitHub maintenance workflows [\#90](https://github.com/Snuffy2/aiopnsense/pull/90) ([Snuffy2](https://github.com/Snuffy2))
- Update prek\_autoupdate.yml [\#86](https://github.com/Snuffy2/aiopnsense/pull/86) ([Snuffy2](https://github.com/Snuffy2))

## [v1.1.5](https://github.com/Snuffy2/aiopnsense/tree/v1.1.5) (2026-07-21)

[Full Changelog](https://github.com/Snuffy2/aiopnsense/compare/v1.1.4...v1.1.5)

**Bug Fixes**

- Add timezone to Speedtest dates [\#83](https://github.com/Snuffy2/aiopnsense/pull/83) ([Snuffy2](https://github.com/Snuffy2))

## [v1.1.4](https://github.com/Snuffy2/aiopnsense/tree/v1.1.4) (2026-07-20)

[Full Changelog](https://github.com/Snuffy2/aiopnsense/compare/v1.1.3...v1.1.4)

**Enhancements**

- Support alternate Speedtest plugins [\#80](https://github.com/Snuffy2/aiopnsense/pull/80) ([Snuffy2](https://github.com/Snuffy2))

**Bug Fixes**

- Parse raw NUT UPS status responses [\#79](https://github.com/Snuffy2/aiopnsense/pull/79) ([Snuffy2](https://github.com/Snuffy2))

## [v1.1.3](https://github.com/Snuffy2/aiopnsense/tree/v1.1.3) (2026-07-16)

[Full Changelog](https://github.com/Snuffy2/aiopnsense/compare/v1.1.2...v1.1.3)

**Bug Fixes**

- Switch firewall rule retrieval to CSV downloads [\#77](https://github.com/Snuffy2/aiopnsense/pull/77) ([Snuffy2](https://github.com/Snuffy2))

## [v1.1.2](https://github.com/Snuffy2/aiopnsense/tree/v1.1.2) (2026-07-15)

[Full Changelog](https://github.com/Snuffy2/aiopnsense/compare/v1.1.1...v1.1.2)

**Enhancements**

- Raise public OPNsense exceptions consistently [\#76](https://github.com/Snuffy2/aiopnsense/pull/76) ([Snuffy2](https://github.com/Snuffy2))

## [v1.1.1](https://github.com/Snuffy2/aiopnsense/tree/v1.1.1) (2026-07-12)

[Full Changelog](https://github.com/Snuffy2/aiopnsense/compare/v1.1.0...v1.1.1)

**Enhancements**

- Allow validation without device identity [\#75](https://github.com/Snuffy2/aiopnsense/pull/75) ([Snuffy2](https://github.com/Snuffy2))

**Documentation**

- Update agent and live script docs [\#73](https://github.com/Snuffy2/aiopnsense/pull/73) ([Snuffy2](https://github.com/Snuffy2))

**Code Quality**

- Trim audit cleanup boilerplate [\#70](https://github.com/Snuffy2/aiopnsense/pull/70) ([Snuffy2](https://github.com/Snuffy2))

**Maintenance**

- Run prek cleanup daily [\#72](https://github.com/Snuffy2/aiopnsense/pull/72) ([Snuffy2](https://github.com/Snuffy2))
- Use reusable prek autoupdate workflow [\#71](https://github.com/Snuffy2/aiopnsense/pull/71) ([Snuffy2](https://github.com/Snuffy2))

## [v1.1.0](https://github.com/Snuffy2/aiopnsense/tree/v1.1.0) (2026-07-03)

[Full Changelog](https://github.com/Snuffy2/aiopnsense/compare/v1.0.15...v1.1.0)

**New Features**

- Add Kea DHCPv6 lease support [\#64](https://github.com/Snuffy2/aiopnsense/pull/64) ([Snuffy2](https://github.com/Snuffy2))
- Add NUT UPS status endpoint [\#63](https://github.com/Snuffy2/aiopnsense/pull/63) ([Snuffy2](https://github.com/Snuffy2))
- Add live OPNsense test scripts [\#62](https://github.com/Snuffy2/aiopnsense/pull/62) ([Snuffy2](https://github.com/Snuffy2))
- Add diagnostics traffic stream support [\#61](https://github.com/Snuffy2/aiopnsense/pull/61) ([Snuffy2](https://github.com/Snuffy2))

**Enhancements**

- Handle OPNsense 26.1.11 API changes [\#69](https://github.com/Snuffy2/aiopnsense/pull/69) ([Snuffy2](https://github.com/Snuffy2))
- Normalize OPNsense NAT and DHCP payloads [\#68](https://github.com/Snuffy2/aiopnsense/pull/68) ([Snuffy2](https://github.com/Snuffy2))

**Code Quality**

- Update agent instructions [\#66](https://github.com/Snuffy2/aiopnsense/pull/66) ([Snuffy2](https://github.com/Snuffy2))

**Maintenance**

- Add issue templates [\#67](https://github.com/Snuffy2/aiopnsense/pull/67) ([Snuffy2](https://github.com/Snuffy2))

## [v1.0.15](https://github.com/Snuffy2/aiopnsense/tree/v1.0.15) (2026-06-28)

[Full Changelog](https://github.com/Snuffy2/aiopnsense/compare/v1.0.14...v1.0.15)

**Bug Fixes**

- Exclude automatic firewall rules [\#59](https://github.com/Snuffy2/aiopnsense/pull/59) ([Snuffy2](https://github.com/Snuffy2))

## [v1.0.14](https://github.com/Snuffy2/aiopnsense/tree/v1.0.14) (2026-06-21)

[Full Changelog](https://github.com/Snuffy2/aiopnsense/compare/v1.0.13...v1.0.14)

**Enhancements**

- Raise missing device unique ID during validation [\#56](https://github.com/Snuffy2/aiopnsense/pull/56) ([Snuffy2](https://github.com/Snuffy2))

**Bug Fixes**

- Preserve deprecated set\_use\_snake\_case compatibility [\#55](https://github.com/Snuffy2/aiopnsense/pull/55) ([Snuffy2](https://github.com/Snuffy2))

## [v1.0.13](https://github.com/Snuffy2/aiopnsense/tree/v1.0.13) (2026-06-21)

[Full Changelog](https://github.com/Snuffy2/aiopnsense/compare/v1.0.12...v1.0.13)

**Bug Fixes**

- Privatize endpoint probe helpers [\#54](https://github.com/Snuffy2/aiopnsense/pull/54) ([Snuffy2](https://github.com/Snuffy2))

## [v1.0.12](https://github.com/Snuffy2/aiopnsense/tree/v1.0.12) (2026-06-20)

[Full Changelog](https://github.com/Snuffy2/aiopnsense/compare/v1.0.11...v1.0.12)

**Bug Fixes**

- Filter invalid SMART devices [\#52](https://github.com/Snuffy2/aiopnsense/pull/52) ([Snuffy2](https://github.com/Snuffy2))

**Code Quality**

- Move OPNsense endpoints to module constants [\#53](https://github.com/Snuffy2/aiopnsense/pull/53) ([Snuffy2](https://github.com/Snuffy2))

## [v1.0.11](https://github.com/Snuffy2/aiopnsense/tree/v1.0.11) (2026-06-20)

[Full Changelog](https://github.com/Snuffy2/aiopnsense/compare/v1.0.10...v1.0.11)

**New Features**

- Add SMART device API support [\#51](https://github.com/Snuffy2/aiopnsense/pull/51) ([Snuffy2](https://github.com/Snuffy2))
- Add CARP maintenance mode toggle [\#47](https://github.com/Snuffy2/aiopnsense/pull/47) ([Snuffy2](https://github.com/Snuffy2))

**Documentation**

- Add Documentation link to pyproject [\#44](https://github.com/Snuffy2/aiopnsense/pull/44) ([Snuffy2](https://github.com/Snuffy2))

**Code Quality**

- Refactor method-aware endpoint availability [\#48](https://github.com/Snuffy2/aiopnsense/pull/48) ([Snuffy2](https://github.com/Snuffy2))

**Maintenance**

- prek autoupdate [\#45](https://github.com/Snuffy2/aiopnsense/pull/45) ([Snuffy2](https://github.com/Snuffy2))

## [v1.0.10](https://github.com/Snuffy2/aiopnsense/tree/v1.0.10) (2026-05-29)

[Full Changelog](https://github.com/Snuffy2/aiopnsense/compare/v1.0.9...v1.0.10)

**Enhancements**

- Improve generated API docstrings [\#41](https://github.com/Snuffy2/aiopnsense/pull/41) ([Snuffy2](https://github.com/Snuffy2))

**Documentation**

- Support PEP 702 deprecations in docs [\#43](https://github.com/Snuffy2/aiopnsense/pull/43) ([Snuffy2](https://github.com/Snuffy2))
- Update README.md [\#42](https://github.com/Snuffy2/aiopnsense/pull/42) ([Snuffy2](https://github.com/Snuffy2))

## [v1.0.9](https://github.com/Snuffy2/aiopnsense/tree/v1.0.9) (2026-05-29)

[Full Changelog](https://github.com/Snuffy2/aiopnsense/compare/v1.0.8...v1.0.9)

**Bug Fixes**

- Map validation endpoint probe errors [\#39](https://github.com/Snuffy2/aiopnsense/pull/39) ([Snuffy2](https://github.com/Snuffy2))
- Fix for kea lease state change in OPNsense 26.1.8 [\#38](https://github.com/Snuffy2/aiopnsense/pull/38) ([Snuffy2](https://github.com/Snuffy2))
- Raise SSL errors during validate call [\#37](https://github.com/Snuffy2/aiopnsense/pull/37) ([HarlemSquirrel](https://github.com/HarlemSquirrel))

**Code Quality**

- Clean up code noise [\#40](https://github.com/Snuffy2/aiopnsense/pull/40) ([Snuffy2](https://github.com/Snuffy2))

## [v1.0.8](https://github.com/Snuffy2/aiopnsense/tree/v1.0.8) (2026-04-06)

[Full Changelog](https://github.com/Snuffy2/aiopnsense/compare/v1.0.7...v1.0.8)

**Maintenance**

- Merge changelog and release actions [\#35](https://github.com/Snuffy2/aiopnsense/pull/35) ([Snuffy2](https://github.com/Snuffy2))

## [v1.0.7](https://github.com/Snuffy2/aiopnsense/tree/v1.0.7) (2026-04-05)

[Full Changelog](https://github.com/Snuffy2/aiopnsense/compare/v1.0.6...v1.0.7)

**Bug Fixes**

- Fix conf.py [\#32](https://github.com/Snuffy2/aiopnsense/pull/32) ([Snuffy2](https://github.com/Snuffy2))

**Documentation**

- Refine changelog [\#33](https://github.com/Snuffy2/aiopnsense/pull/33) ([Snuffy2](https://github.com/Snuffy2))
- Further documentation refinement [\#31](https://github.com/Snuffy2/aiopnsense/pull/31) ([Snuffy2](https://github.com/Snuffy2))

## [v1.0.6](https://github.com/Snuffy2/aiopnsense/tree/v1.0.6) (2026-04-05)

[Full Changelog](https://github.com/Snuffy2/aiopnsense/compare/v1.0.5...v1.0.6)

**Enhancements**

- Add back legacy unbound blocklist support [\#26](https://github.com/Snuffy2/aiopnsense/pull/26) ([Snuffy2](https://github.com/Snuffy2))
- Add back legacy camelCase support [\#25](https://github.com/Snuffy2/aiopnsense/pull/25) ([Snuffy2](https://github.com/Snuffy2))
- Add validate method [\#23](https://github.com/Snuffy2/aiopnsense/pull/23) ([Snuffy2](https://github.com/Snuffy2))
- Add endpoint checks to prevent errors [\#22](https://github.com/Snuffy2/aiopnsense/pull/22) ([Snuffy2](https://github.com/Snuffy2))
- Firewall endpoints to GET and don't fail if unavailable [\#21](https://github.com/Snuffy2/aiopnsense/pull/21) ([Snuffy2](https://github.com/Snuffy2))

**Bug Fixes**

- Fix showing docstrings in documentation [\#29](https://github.com/Snuffy2/aiopnsense/pull/29) ([Snuffy2](https://github.com/Snuffy2))

**Documentation**

- Clarify opts documentation [\#30](https://github.com/Snuffy2/aiopnsense/pull/30) ([Snuffy2](https://github.com/Snuffy2))
- Introduce GitHub Actions changelog workflow and reorganize docs [\#28](https://github.com/Snuffy2/aiopnsense/pull/28) ([Snuffy2](https://github.com/Snuffy2))
- Add documentation [\#27](https://github.com/Snuffy2/aiopnsense/pull/27) ([Snuffy2](https://github.com/Snuffy2))

**Code Quality**

- Finish rename to aiopnsense [\#24](https://github.com/Snuffy2/aiopnsense/pull/24) ([Snuffy2](https://github.com/Snuffy2))
- Change to prek.toml [\#20](https://github.com/Snuffy2/aiopnsense/pull/20) ([Snuffy2](https://github.com/Snuffy2))

## [v1.0.5](https://github.com/Snuffy2/aiopnsense/tree/v1.0.5) (2026-04-03)

[Full Changelog](https://github.com/Snuffy2/aiopnsense/compare/v1.0.4...v1.0.5)

**Enhancements**

- In OPNsenseClient, change from initial to throw\_errors [\#19](https://github.com/Snuffy2/aiopnsense/pull/19) ([Snuffy2](https://github.com/Snuffy2))

## [v1.0.4](https://github.com/Snuffy2/aiopnsense/tree/v1.0.4) (2026-03-29)

[Full Changelog](https://github.com/Snuffy2/aiopnsense/compare/v1.0.3...v1.0.4)

**Enhancements**

- Add CARP status summary and improve VIP interface matching [\#16](https://github.com/Snuffy2/aiopnsense/pull/16) ([Snuffy2](https://github.com/Snuffy2))

**Bug Fixes**

- Further CARP refinement [\#17](https://github.com/Snuffy2/aiopnsense/pull/17) ([Snuffy2](https://github.com/Snuffy2))

**Maintenance**

- Update release Github Action [\#18](https://github.com/Snuffy2/aiopnsense/pull/18) ([Snuffy2](https://github.com/Snuffy2))

## [v1.0.3](https://github.com/Snuffy2/aiopnsense/tree/v1.0.3) (2026-03-26)

[Full Changelog](https://github.com/Snuffy2/aiopnsense/compare/v1.0.2...v1.0.3)

**Bug Fixes**

- Handle 26.1.5+ changes in dnsmasq leases API [\#14](https://github.com/Snuffy2/aiopnsense/pull/14) ([Snuffy2](https://github.com/Snuffy2))

**Code Quality**

- Turn down more debug logging [\#15](https://github.com/Snuffy2/aiopnsense/pull/15) ([Snuffy2](https://github.com/Snuffy2))

## [v1.0.2](https://github.com/Snuffy2/aiopnsense/tree/v1.0.2) (2026-03-23)

[Full Changelog](https://github.com/Snuffy2/aiopnsense/compare/v1.0.1...v1.0.2)

**Enhancements**

- Expand ambiguous time zone list [\#12](https://github.com/Snuffy2/aiopnsense/pull/12) ([Snuffy2](https://github.com/Snuffy2))

**Code Quality**

- Turn down firewall debug logging [\#11](https://github.com/Snuffy2/aiopnsense/pull/11) ([Snuffy2](https://github.com/Snuffy2))

## [v1.0.1](https://github.com/Snuffy2/aiopnsense/tree/v1.0.1) (2026-03-23)

[Full Changelog](https://github.com/Snuffy2/aiopnsense/compare/v1.0.0...v1.0.1)

**Documentation**

- Update Documentation [\#10](https://github.com/Snuffy2/aiopnsense/pull/10) ([Snuffy2](https://github.com/Snuffy2))

## [v1.0.0](https://github.com/Snuffy2/aiopnsense/tree/v1.0.0) (2026-03-22)

[Full Changelog](https://github.com/Snuffy2/aiopnsense/compare/v0.0.3...v1.0.0)

## [v0.0.3](https://github.com/Snuffy2/aiopnsense/tree/v0.0.3) (2026-03-22)

[Full Changelog](https://github.com/Snuffy2/aiopnsense/compare/v0.0.2...v0.0.3)

**Enhancements**

- Improve pytest coverage and Refactor test infrastructure with reusable HTTP response mocking [\#8](https://github.com/Snuffy2/aiopnsense/pull/8) ([Snuffy2](https://github.com/Snuffy2))

**Bug Fixes**

- Fix some endpoints from POST to GET [\#6](https://github.com/Snuffy2/aiopnsense/pull/6) ([Snuffy2](https://github.com/Snuffy2))
- Fix GitHub release Workflow [\#4](https://github.com/Snuffy2/aiopnsense/pull/4) ([Snuffy2](https://github.com/Snuffy2))

**Documentation**

- Standardize docstring formatting across codebase [\#7](https://github.com/Snuffy2/aiopnsense/pull/7) ([Snuffy2](https://github.com/Snuffy2))

**Other Changes**

- Handle if DHCP endpoints are unavailable [\#9](https://github.com/Snuffy2/aiopnsense/pull/9) ([Snuffy2](https://github.com/Snuffy2))
- Remove legacy code [\#5](https://github.com/Snuffy2/aiopnsense/pull/5) ([Snuffy2](https://github.com/Snuffy2))

## [v0.0.2](https://github.com/Snuffy2/aiopnsense/tree/v0.0.2) (2026-03-22)

[Full Changelog](https://github.com/Snuffy2/aiopnsense/compare/v0.0.1...v0.0.2)

**Other Changes**

- Update version in const.py on release [\#3](https://github.com/Snuffy2/aiopnsense/pull/3) ([Snuffy2](https://github.com/Snuffy2))

## [v0.0.1](https://github.com/Snuffy2/aiopnsense/tree/v0.0.1) (2026-03-22)

[Full Changelog](https://github.com/Snuffy2/aiopnsense/compare/0931722e696e8e3270e8b80682ee8ff74ab0f5bb...v0.0.1)

**Other Changes**

- Create AGENTS.md [\#2](https://github.com/Snuffy2/aiopnsense/pull/2) ([Snuffy2](https://github.com/Snuffy2))
- GitHub Workflows and Linting [\#1](https://github.com/Snuffy2/aiopnsense/pull/1) ([Snuffy2](https://github.com/Snuffy2))



\* *This Changelog was automatically generated by [github_changelog_generator](https://github.com/github-changelog-generator/github-changelog-generator)*
