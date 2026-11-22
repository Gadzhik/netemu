"""Ready-made DDoS labs (20-24).  Imported by make_labs.py; each returns
(topology, file name, title, task, checks, solution script, notes).

The common stand: a company site (EDGE router, switch, web server behind NAT) is connected to the
Internet cloud on ISP0 (203.0.113.2); a customer of the same provider (CLIENT, 203.0.113.10 on ISP2)
uses the site; a botnet sits on ISP3.  The attacks are already running when the lab opens."""
from __future__ import annotations

from labs_more import _cli, _host

from ipaddress import IPv4Address

from netemu.core.security.ddos import Attack
from netemu.core.topology import Topology

SITE = "203.0.113.2"


def stand(web_model="server"):
    t = Topology()
    inet = t.add_device("internet", "ISP", 420, 80)
    edge = t.add_device("cisco-2911", "EDGE", 420, 260)
    sw = t.add_device("cisco-2960", "SW1", 420, 420)
    web = t.add_device(web_model, "WEB", 300, 560)
    client = t.add_device("pc", "CLIENT", 120, 80)
    bots = t.add_device("botnet", "BOTNET", 720, 80)
    t.connect(edge.get_port("gi0/1"), inet.get_port("ISP0"))
    t.connect(client.nic, inet.get_port("ISP2"))
    t.connect(bots.nic, inet.get_port("ISP3"))
    t.connect(edge.get_port("gi0/0"), sw.get_port("Gi0/1"))
    t.connect(web.nic, sw.get_port("Gi0/2"))
    _host(client, "203.0.113.10/30", "203.0.113.9")
    _host(bots, "203.0.113.14/30", "203.0.113.13")
    _host(web, "192.168.1.10/24", "192.168.1.1")
    web.http_server.pages["index.html"] = ("<html><body><h1>Shop</h1><p>The company's online shop.</p></body></html>")
    _cli(edge, f"""enable
configure terminal
hostname EDGE
interface gi0/0
ip address 192.168.1.1 255.255.255.0
ip nat inside
no shutdown
interface gi0/1
ip address {SITE} 255.255.255.252
ip nat outside
no shutdown
exit
ip route 0.0.0.0 0.0.0.0 203.0.113.1
ip nat inside source static tcp 192.168.1.10 80 {SITE} 80
end""")
    return t, inet, edge, web, client, bots


AVAILABLE = f"available CLIENT http://{SITE} 90 | The shop opens for customers (9 of 10 requests)"


def volumetric():
    t, inet, edge, web, client, bots = stand()
    bots.attacks = [Attack("udp", SITE, 80, bots=30000, rate=10, size=1250, spoof=True, running=True)]  # 3 Gbit/s
    text = f"""**Объёмная атака.** Ботнет (30 000 машин) шлёт на адрес сайта {SITE} UDP-поток **3 Гбит/с** с подменёнными адресами. Канал сайта к провайдеру — 1 Гбит/с, он забит втрое: две трети любых пакетов теряются, магазин у покупателя (CLIENT) не открывается.

1. Откройте «Инструменты → Нагрузка и DDoS»: найдите перегруженный кабель (он красный на схеме) и тип атаки.
2. Попробуйте фильтр на своём маршрутизаторе EDGE (ACL `deny udp any host {SITE}` на gi0/1). Убедитесь, что это **не помогает**: канал забит до EDGE, а адреса подменены, блокировать по источнику нечего.
3. Включите защиту у провайдера: облако ISP → вкладка «Защита провайдера» → **очистка трафика** для порта ISP0. Центр очистки убирает поток атаки до вашего канала, легитимный трафик проходит.
4. Проверьте задание и загрузку кабелей."""
    checks = f"""{AVAILABLE}
ddos EDGE 50 | Атака не доходит до маршрутизатора EDGE (меньше 50 Мбит/с)"""
    script = {"ISP": "@isp ISP0 scrub on"}
    return t, "20-ddos-volumetric", "DDoS: объёмная атака и очистка у провайдера", text, \
        checks.replace("The shop opens for customers (9 of 10 requests)", "Магазин открывается у покупателя (9 из 10 запросов)"), \
        script, "Атаку, которая забивает канал, нельзя отфильтровать на своей стороне канала: это делает провайдер (scrubbing, FlowSpec, RTBH)."


def syn_flood():
    t, inet, edge, web, client, bots = stand("server-linux")
    bots.attacks = [Attack("syn", SITE, 80, bots=200, rate=100, spoof=True, running=True)]   # 20 kpps, ~10 Mbit/s
    text = f"""**SYN-флуд.** Ботнет шлёт на веб-сервер 20 000 SYN в секунду с подменёнными адресами — всего около 10 Мбит/с, канал почти свободен. Но сервер выделяет память на каждое полуоткрытое соединение и ждёт ACK, который никогда не придёт: очередь (backlog) переполнена, новые клиенты не могут подключиться.

Включите одну из защит (лучше понять обе):
- на сервере WEB (Linux): **SYN cookies** — `sudo sysctl -w net.ipv4.tcp_syncookies=1`; сервер не хранит состояние, пока клиент не ответит;
- на маршрутизаторе EDGE: **TCP intercept** — `access-list 120 permit tcp any host {SITE} eq 80` и `ip tcp intercept list 120`: маршрутизатор сам завершает рукопожатие и пропускает к серверу только настоящие соединения.

Почему здесь бесполезен ACL по источнику? Почему не помогает uRPF на EDGE?"""
    checks = f"available CLIENT http://{SITE} 90 | Магазин открывается у покупателя (9 из 10 запросов)"
    script = {"WEB": "sudo sysctl -w net.ipv4.tcp_syncookies=1"}
    return t, "21-ddos-syn-flood", "DDoS: SYN-флуд, SYN cookies и TCP intercept", text, checks, script, \
        "Атака на ресурсы протокола (TCP state), а не на канал. Защита: SYN cookies на сервере или TCP intercept/SYN proxy перед ним."


def amplification():
    t, inet, edge, web, client, bots = stand()
    bots.attacks = [Attack("dns-amp", SITE, 53, bots=1000, rate=100, reflectors=["198.51.100.53"], running=True)]
    text = f"""**Отражённая атака с усилением (DNS amplification).** Боты отправляют маленькие DNS-запросы (64 байта, около 50 Мбит/с) на чужой **открытый резолвер** 198.51.100.53, подставив в источник адрес вашего сайта {SITE}. Резолвер честно отвечает — ответами по 3 000 байт. На сайт приходит **≈2,4 Гбит/с**: канал 1 Гбит/с забит.

1. В «Нагрузка и DDoS» убедитесь, что поток идёт не от ботов, а от резолвера, с порта-источника 53.
2. Подумайте, почему бесполезно: блокировать адреса ботов (их не видно), ACL на EDGE (канал уже забит), выключать свой DNS-сервер (резолвер чужой).
3. Попросите провайдера отбрасывать ответы DNS до вашего канала: облако ISP → «Защита провайдера» → FlowSpec для ISP0: `udp src 53`. (У вашего сайта нет своих DNS-запросов к внешним серверам — легитимному трафику это не мешает.)
4. Альтернативы: очистка трафика (scrubbing) или RTBH (сайт станет недоступен, но канал освободится)."""
    checks = f"""available CLIENT http://{SITE} 90 | Магазин открывается у покупателя (9 из 10 запросов)
ddos EDGE 50 | Отражённый поток не доходит до EDGE"""
    script = {"ISP": "@isp ISP0 flowspec udp src 53"}
    return t, "22-ddos-amplification", "DDoS: отражённая атака с усилением (DNS)", text, checks, script, \
        "Коэффициенты усиления: DNS ANY ≈ 28–54, NTP monlist ≈ 556, Memcached до 51 000. Лечится закрытием открытых резолверов в интернете и фильтрацией у провайдера."


def application():
    t, inet, edge, web, client, bots = stand()
    bots.attacks = [Attack("http", SITE, 80, bots=1000, rate=2, running=True),
                    Attack("slowloris", SITE, 80, bots=20, rate=30, running=True)]
    text = """**Атаки прикладного уровня (L7).** Трафика мало, каналы свободны, очистка у провайдера не видит ничего подозрительного: запросы настоящие.

- **HTTP-флуд**: 1 000 ботов делают по 2 запроса в секунду — 2 000 запросов/с при производительности сервера 300 запросов/с. Сервер отвечает «503 Service Unavailable».
- **Slowloris**: 20 ботов держат по 30 медленных соединений — 600 при лимите сервера 256. Новых клиентов просто некуда принять.

На сервере WEB (вкладка «Службы» → «Защита от DDoS»):
1. Попробуйте **лимит запросов с одного IP** = 1 в секунду. Почему этого мало? (1 000 ботов × 1 запрос/с.)
2. Включите **проверку браузера (JS-challenge)**: боты не выполняют JavaScript, их запросы отсеиваются.
3. Против Slowloris включите **лимит соединений с одного IP** (например, 10)."""
    checks = f"available CLIENT http://{SITE} 90 | Магазин открывается у покупателя (9 из 10 запросов)"
    script = {"WEB": "@ddos challenge on\n@ddos connlimit 10"}
    return t, "23-ddos-application", "DDoS: HTTP-флуд и Slowloris (L7)", text, checks, script, \
        "Против L7: rate limiting, JS-challenge/CAPTCHA, WAF, кэширование и CDN, ограничение соединений и таймауты заголовков на reverse proxy."


def antispoofing():
    t = Topology()
    isp = t.add_device("cisco-2911", "ISP-R", 400, 220)
    sw = t.add_device("cisco-2960", "CUST-SW", 160, 380)
    bots = t.add_device("botnet", "INFECTED", 80, 540)
    pc = t.add_device("pc", "CUST-PC", 260, 540)
    victim = t.add_device("server", "VICTIM", 660, 380)
    t.connect(isp.get_port("gi0/0"), sw.get_port("Gi0/1"))
    t.connect(bots.nic, sw.get_port("Fa0/1"))
    t.connect(pc.nic, sw.get_port("Fa0/2"))
    t.connect(victim.nic, isp.get_port("gi0/1"))
    _host(bots, "198.51.100.66/26", "198.51.100.65")
    _host(pc, "198.51.100.70/26", "198.51.100.65")
    _host(victim, "192.0.2.10/24", "192.0.2.1")
    _cli(isp, """enable
configure terminal
hostname ISP-R
interface gi0/0
description customer 198.51.100.64/26
ip address 198.51.100.65 255.255.255.192
no shutdown
interface gi0/1
ip address 192.0.2.1 255.255.255.0
no shutdown
end""")
    bots.attacks = [Attack("udp", "192.0.2.10", 80, bots=50, rate=100, size=1000, spoof=True, running=True)]
    text = """**Антиспуфинг (BCP 38).** Вы — инженер провайдера. У клиента (сеть 198.51.100.64/26 на интерфейсе gi0/0 маршрутизатора ISP-R) заражённые машины атакуют сервер VICTIM, **подделывая адреса источника** — жертва видит «случайные» адреса со всего интернета и не может их заблокировать.

По BCP 38 провайдер не должен выпускать из сети клиента пакеты с чужими адресами:
1. На ISP-R на интерфейсе к клиенту включите строгий uRPF: `ip verify unicast source reachable-via rx`. Маршрутизатор отбросит пакет, если обратный маршрут к его источнику ведёт не через этот же интерфейс.
2. Убедитесь, что легитимный трафик клиента (CUST-PC) по-прежнему ходит, а поток с подменёнными адресами до VICTIM не доходит.
3. Почему строгий uRPF нельзя включать на интерфейсе к вышестоящему провайдеру с маршрутом по умолчанию и при асимметричной маршрутизации? Чем loose-режим (`reachable-via any`) отличается?"""
    checks = """ddos VICTIM 1 | Поток с подменёнными адресами не доходит до VICTIM
ping CUST-PC 192.0.2.10 | Легитимный трафик клиента проходит"""
    script = {"ISP-R": "enable\nconfigure terminal\ninterface gi0/0\nip verify unicast source reachable-via rx\nend"}
    return t, "24-ddos-antispoofing", "DDoS: антиспуфинг uRPF (BCP 38)", text, checks, script, \
        "uRPF на всех клиентских интерфейсах провайдеров сделал бы невозможными атаки с подменой адреса и отражённые атаки."


def dns_switch():
    t, inet, edge, web, client, bots = stand()
    client.dns = bots.dns = IPv4Address("8.8.8.8")
    inet.set_zone("shop.example", SITE, ttl=30)
    bots.attacks = [Attack("udp", "shop.example", 80, bots=30000, rate=10, size=1250, spoof=True, running=True),
                    Attack("http", "shop.example", 80, bots=1000, rate=2, running=True)]
    text = """**Переключение DNS на сервис защиты.** Магазин доступен по имени **shop.example**, домен обслуживает DNS провайдера (облако ISP, TTL записи 30 секунд). Ботнет атакует **по имени**: UDP-флуд 3 Гбит/с и HTTP-флуд 2 000 запросов/с. Канал 1 Гбит/с забит, магазин не открывается.

У провайдера есть сервис защиты (адрес 198.51.100.200) — обратный прокси с огромной ёмкостью: он поглощает флуд, отсеивает ботов и сам забирает страницы с вашего сервера.
1. Облако ISP → «Защита провайдера» → таблица DNS-хостинга: отметьте для shop.example **«Через сервис защиты»** и примените. Теперь резолверы отвечают адресом прокси.
2. Клиенты и боты узнают новый адрес, только когда истечёт TTL записи в их кэше. На CLIENT посмотрите `ipconfig /displaydns`, подождите 30 секунд или очистите кэш: `ipconfig /flushdns`.
3. Посмотрите «Нагрузка и DDoS»: поток атаки идёт к 198.51.100.200 и заканчивается в сети провайдера, канал сайта свободен.
4. Подумайте: что будет, если атакующий узнает настоящий адрес 203.0.113.2 (из старых записей DNS, заголовков писем, поддоменов)? Почему перед атакой полезно заранее уменьшить TTL? Как принимать HTTP только от сервиса защиты (ACL на EDGE)?"""
    checks = """available CLIENT http://shop.example 90 | Магазин открывается у покупателя по имени (9 из 10 запросов)
ddos EDGE 50 | Атака не доходит до маршрутизатора EDGE (меньше 50 Мбит/с)"""
    script = {"ISP": "@isp dns shop.example shield on", "CLIENT": "ipconfig /flushdns"}
    return t, "25-ddos-dns-switch", "DDoS: переключение DNS на сервис защиты", text, checks, script, \
        "Переключение DNS (или BGP-анонс через центр очистки) переводит трафик на сервис защиты. Работает против " \
        "атак по имени; настоящий адрес сервера нужно скрывать, а TTL держать небольшим."


def _fix(fn):
    def wrapper():
        t, fname, title, text, checks, script, notes = fn()
        for d in t.devices.values():                 # the attacks are running when the lab opens
            if getattr(d, "attacks", None):
                d.start()
        return t, fname, title, text, checks.replace(
            "The shop opens for customers (9 of 10 requests)", "Магазин открывается у покупателя (9 из 10 запросов)"), \
            script, notes
    wrapper.__name__ = fn.__name__
    return wrapper


LABS = [_fix(volumetric), _fix(syn_flood), _fix(amplification), _fix(application), _fix(antispoofing),
        _fix(dns_switch)]

SHOP = "The shop opens for customers (9 of 10 requests)"
EN = {
    "20-ddos-volumetric": {"title": "DDoS: volumetric attack and scrubbing at the provider", "text": f"""**Volumetric attack.** A botnet (30,000 machines) sends a **3 Gbit/s** UDP stream with spoofed sources to the site's address {SITE}. The site's link to the provider is 1 Gbit/s and is overloaded three times: two thirds of all packets are lost, the shop does not open for the customer (CLIENT).

1. Open Tools → Load and DDoS: find the overloaded cable (red on the canvas) and the attack kind.
2. Try a filter on your own router EDGE (ACL `deny udp any host {SITE}` on gi0/1). See that it **does not help**: the link is full before EDGE, and the sources are spoofed, there is nothing to block by source.
3. Turn on the provider's protection: ISP cloud → Provider protection tab → **scrubbing** for port ISP0. The scrubbing centre removes the attack before your link; legitimate traffic passes.
4. Check the lab and the cable load.""",
                           "checks": [SHOP, "The attack does not reach EDGE (below 50 Mbit/s)"]},
    "21-ddos-syn-flood": {"title": "DDoS: SYN flood, SYN cookies and TCP intercept", "text": f"""**SYN flood.** The botnet sends the web server 20,000 SYNs per second from spoofed addresses: about 10 Mbit/s, the link is almost idle. But the server reserves memory for every half-open connection and waits for an ACK that never comes: the backlog is full, new clients cannot connect.

Turn on one of the protections (better understand both):
- on the WEB server (Linux): **SYN cookies**, `sudo sysctl -w net.ipv4.tcp_syncookies=1`; the server keeps no state until the client answers;
- on the EDGE router: **TCP intercept**, `access-list 120 permit tcp any host {SITE} eq 80` and `ip tcp intercept list 120`: the router completes the handshake itself and lets only real connections through.

Why is an ACL by source useless here? Why does uRPF on EDGE not help?""",
                          "checks": [SHOP]},
    "22-ddos-amplification": {"title": "DDoS: reflection and amplification (DNS)", "text": f"""**Reflection with amplification (DNS amplification).** The bots send small DNS queries (64 bytes, about 50 Mbit/s) to someone else's **open resolver** 198.51.100.53 with your site's address {SITE} as the source. The resolver answers honestly, with 3,000-byte responses. **About 2.4 Gbit/s** reaches the site: the 1 Gbit/s link is full.

1. In Load and DDoS see that the flow comes from the resolver, from source port 53, not from the bots.
2. Think why these are useless: blocking the bots' addresses (you do not see them), an ACL on EDGE (the link is already full), turning off your own DNS server (the resolver is not yours).
3. Ask the provider to drop DNS responses before your link: ISP cloud → Provider protection → FlowSpec for ISP0: `udp src 53`. (Your site sends no DNS queries to outside servers, so legitimate traffic is not affected.)
4. Alternatives: scrubbing or RTBH (the site becomes unreachable but the link is free).""",
                              "checks": [SHOP, "The reflected flow does not reach EDGE"]},
    "23-ddos-application": {"title": "DDoS: HTTP flood and Slowloris (L7)", "text": """**Application-layer attacks (L7).** Little traffic, the links are free, scrubbing at the provider sees nothing suspicious: the requests are real.

- **HTTP flood**: 1,000 bots send 2 requests per second each: 2,000 requests/s against a server that serves 300 requests/s. The server answers "503 Service Unavailable".
- **Slowloris**: 20 bots keep 30 slow connections each: 600 against the server's limit of 256. New customers have nowhere to go.

On the WEB server (Services tab → DDoS protection):
1. Try a **per-IP request limit** of 1 per second. Why is it not enough? (1,000 bots × 1 request/s.)
2. Turn on the **browser check (JS challenge)**: bots do not run JavaScript, their requests are filtered out.
3. Against Slowloris set a **per-IP connection limit** (e.g. 10).""",
                            "checks": [SHOP]},
    "24-ddos-antispoofing": {"title": "DDoS: anti-spoofing with uRPF (BCP 38)", "text": """**Anti-spoofing (BCP 38).** You are a provider's engineer. In a customer's network (198.51.100.64/26 on gi0/0 of ISP-R) infected machines attack the VICTIM server with **forged source addresses**: the victim sees "random" addresses from all over the Internet and cannot block them.

By BCP 38 a provider must not let packets with foreign addresses out of a customer's network:
1. On ISP-R, on the interface towards the customer, turn on strict uRPF: `ip verify unicast source reachable-via rx`. The router drops a packet when the route back to its source does not go through that same interface.
2. Check that the customer's legitimate traffic (CUST-PC) still flows while the spoofed stream no longer reaches VICTIM.
3. Why must strict uRPF not be used towards an upstream provider with a default route, or with asymmetric routing? How does loose mode (`reachable-via any`) differ?""",
                             "checks": ["The spoofed stream does not reach VICTIM", "The customer's legitimate traffic passes"]},
    "25-ddos-dns-switch": {"title": "DDoS: switching DNS to the protection service", "text": """**Switching DNS to the protection service.** The shop is reached by the name **shop.example**; the domain is served by the provider's DNS (the ISP cloud, record TTL 30 seconds). The botnet attacks **by name**: a 3 Gbit/s UDP flood and an HTTP flood of 2,000 requests/s. The 1 Gbit/s link is full, the shop does not open.

The provider offers a protection service (address 198.51.100.200): a reverse proxy with huge capacity that absorbs floods, filters out bots and fetches the pages from your server itself.
1. ISP cloud → Provider protection → the DNS hosting table: tick **Through the protection service** for shop.example and apply. The resolvers now answer with the proxy's address.
2. Clients and bots learn the new address only when the TTL of the record in their cache runs out. On CLIENT look at `ipconfig /displaydns`, wait 30 seconds or clear the cache: `ipconfig /flushdns`.
3. Look at Load and DDoS: the attack now goes to 198.51.100.200 and ends inside the provider's network; the site's link is free.
4. Think: what happens if the attacker learns the real address 203.0.113.2 (from old DNS records, mail headers, subdomains)? Why lower the TTL in advance? How do you accept HTTP only from the protection service (an ACL on EDGE)?""",
                           "checks": ["The shop opens for the customer by name (9 of 10 requests)",
                                      "The attack does not reach EDGE (below 50 Mbit/s)"]},
}
