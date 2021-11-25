# coding:utf8
"""
下载器
"""
import os
import time
import random
from collections import deque
from typing import List, Dict, Union

import httpx
import redis


try:
    from user_agent2 import generate_user_agent
except ImportError:
    generate_user_agent = None

from graper import util
from graper.utils import log
from graper.network import proxy

logger = log.get_logger(__file__)


class UserAgentPool(util.RequestArgsPool):
    # device types
    pc = "pc"
    #
    win = "win"
    mac = "mac"
    linux = "linux"
    compatible = "compatible"

    mobile = "mobile"
    #
    android = "android"
    ios = "ios"
    #
    dev_types = [pc, mobile, win, mac, linux, android, ios]

    def __init__(
        self, types=None, with_random_ua=True, ua_local_dir: str = "", **kwargs
    ):
        """
        Args:
            types:
            with_random_ua:
                True: UA was generated by user_agent.generate_user_agent
                False: UA was generated by the user special local files
            ua_local_dir:
                UA files dirname. default is ~/.graper/ua, and filename only support:
                    windows.txt
                    mac.txt
                    linux.txt
                    compatible.txt
                    android.txt
                    ios.txt
            **kwargs:
        """
        super().__init__(**kwargs)
        if not types:
            # 默认pc
            types = [self.win, self.mac]
        self.types = types
        self.with_random_ua = with_random_ua

        self.user_agents = []
        self.init_flag = 0
        # ua文件目录
        self.ua_local_dir = ua_local_dir or os.path.join(
            os.path.expanduser("~"), ".graper", "ua"
        )
        self.__types = []

    def init(self):
        self.init_flag = 1

        type_func_dict = {
            self.pc: self.pc_ua,
            self.mobile: self.mobile_ua,
            self.win: self.win_ua,
            self.mac: self.mac_ua,
            self.linux: self.linux_ua,
            self.android: self.android_ua,
            self.ios: self.ios_ua,
        }
        for typ in self.types:
            ua_list = type_func_dict[typ]
            if ua_list:
                self.user_agents.extend(ua_list)

            if typ == self.pc:
                self.__types.extend([self.win, self.mac, self.linux])
            elif typ == self.mobile:
                self.__types.extend([self.android])
            else:
                self.__types.extend([typ])

        self.__types = [x for x in self.__types if x not in [self.compatible, self.ios]]
        return

    def get_ua_from_file(self, filename) -> List[str]:
        """
            Get UA string from local file, each line will be used as an UA string
            eg.
                get_ua_from_files("windows.txt")
        Args:
            filename:

        Returns:

        """
        ua_list = []
        ua_file = os.path.join(self.ua_local_dir, filename)
        if os.path.isfile(ua_file):
            with open(ua_file, encoding="utf8") as f:
                lines = f.readlines()
            ua_list = [x.strip() for x in lines if x.strip()]
        return ua_list

    def get(self):
        """
            Get a random UA string
        Returns:

        """

        if not self.init_flag:
            self.init()
        if self.with_random_ua and generate_user_agent:
            ua = generate_user_agent(os=tuple(self.__types))
            return ua
        return random.choice(self.user_agents) if self.user_agents else ""

    @property
    def pc_ua(self):
        return self.win_ua + self.mac_ua + self.linux_ua

    @property
    def win_ua(self):
        return self.get_ua_from_file("windows.txt")

    @property
    def mac_ua(self):
        return self.get_ua_from_file("mac.txt")

    @property
    def compatible_ua(self):
        return self.get_ua_from_file("compatible.txt")

    @property
    def linux_ua(self):
        return self.get_ua_from_file("linux.txt")

    @property
    def mobile_ua(self):
        return self.android_ua + self.ios_ua

    @property
    def android_ua(self):
        return self.get_ua_from_file("android.txt")

    @property
    def ios_ua(self):
        return self.get_ua_from_file("ios.txt")


class CookiePool(util.RequestArgsPool):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.cookie_list = deque(maxlen=10000)

    def get(self):
        try:
            r = self.cookie_list.pop()
        except Exception:
            r = None
        return r

    def add(self, cookies):
        self.cookie_list.append(cookies)
        return

    def __len__(self):
        return len(self.cookie_list)


class LimitRedisCookiePool(util.RequestArgsPool):
    """
    Use Redis zset to limit cookie use interval
    """

    def __init__(
        self,
        redis_conn: redis.Redis,
        namespace: str,
        limit: int,
        with_random=0,
        **kwargs,
    ):
        """

        Args:
            redis_conn:
            namespace: Unique identifier for different application
            limit: cookie usage intervals, unit is second
            with_random: random fluctuation of limit
            **kwargs:
        """
        super().__init__(**kwargs)
        self.redis_conn = redis_conn
        assert namespace, "must special namespace"
        self.cookie_key = f"LimitRedisCookiePool:{namespace}"
        self.limit = limit
        self.with_random = with_random
        assert self.with_random >= 0

    def get(self, retry=3):
        cookie = None
        _limit = self.limit
        if self.with_random:
            _limit += random.randint(-self.with_random, self.with_random)
        for i in range(retry):
            cookie_list = self.redis_conn.zrangebyscore(
                self.cookie_key, "-inf", time.time() - _limit
            )
            if not cookie_list:
                time.sleep(1)
                continue
            else:
                cookie = random.choice(cookie_list).decode()
                self.redis_conn.zadd(self.cookie_key, **{cookie: time.time()})
                break
        return cookie

    def add(self, cookie: str, delay=0):
        """
        Args:
            cookie:
            delay: cookie can be used after delay seconds

        Returns:

        """
        if not isinstance(cookie, list):
            cookie = [cookie]
        info = {k: time.time() + delay for k in cookie}
        self.redis_conn.zadd(self.cookie_key, **info)
        return

    def delete(self, cookie: str):
        self.redis_conn.zrem(self.cookie_key, cookie)
        return


class RefererPool(util.RequestArgsPool):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)


default_user_agent_pool = UserAgentPool()
default_cookie_pool = CookiePool()
default_referer_pool = RefererPool()


class Downloader(object):
    def __init__(
        self,
        proxy_enable: bool = True,
        timeout: int = 20,
        proxy_pool=proxy.default_proxy_pool,
        cookie_pool=None,
        user_agent_pool=default_user_agent_pool,
        referer_pool=None,
        show_error_log: bool = False,
        show_fail_log: bool = True,
        use_session: bool = True,
        stream: bool = False,
        http2: bool = False,
        use_default_headers: bool = True,
        format_headers: bool = True,
        **kwargs,
    ):
        """
        下载器
        Args:
            proxy_enable: whether use proxy
            timeout: download timeout
            proxy_pool:
            cookie_pool:
            user_agent_pool:
            referer_pool:
            show_error_log: whether show exception's stack message
            show_fail_log: whether show download failed log
            use_session:
            stream:
            http2: use http/2
            use_default_headers:
            format_headers:
            **kwargs:
        """
        super().__init__()

        #
        self.http2 = http2
        self.timeout = timeout
        self.proxy_enable = proxy_enable
        self.proxy_pool: proxy.ProxyPool = proxy_pool
        self.user_agent_pool = user_agent_pool
        self.cookie_pool: CookiePool = cookie_pool
        self.referer_pool = referer_pool
        self.show_error_log = show_error_log
        self.show_fail_log = show_fail_log
        #
        self.use_session = use_session
        self.stream = stream
        self.use_default_headers = use_default_headers
        self.format_headers = format_headers
        self.kwargs = kwargs

        self.session = self.make_httpx_client()

        # User Define
        self._custom_headers = {}

    def close(self):
        for pool in [self.cookie_pool, self.referer_pool, self.user_agent_pool]:
            if pool:
                try:
                    pool.close()
                except:
                    pass
        self.session.close()

    def add_headers(self, headers: Dict):
        """
            Add headers to default headers

        Args:
            headers:

        Returns:

        """
        self._custom_headers.update(util.format_headers(headers))

    @property
    def default_headers(self):
        headers = {
            "User-Agent": self.user_agent_pool.get() if self.user_agent_pool else "",
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }
        headers.update(self._custom_headers)
        return headers

    @staticmethod
    def convert_http_protocol(request):
        """
            http => https
            https => http
        Args:
            request:

        Returns:

        """
        if isinstance(request, dict):
            url = request.get("url")
        else:
            url = request
        if url.startswith("https"):
            url = "http" + url[5:]
        elif url.startswith("http:"):
            url = "https" + url[4:]
        if isinstance(request, dict):
            request["url"] = url
        else:
            request = url
        return request

    def make_httpx_client(self, **kwargs):
        if "limits" not in kwargs:
            kwargs["limits"] = httpx.Limits(
                max_connections=1000, max_keepalive_connections=1000,
            )
        if "http2" not in kwargs:
            kwargs["http2"] = self.http2

        return httpx.Client(**kwargs)

    def prepare_request(self, request: Union[str, Dict], **kwargs):
        """

        Args:
            request:
            **kwargs:

        Returns:

        """
        url = request
        if isinstance(request, dict):
            url = request.get("url")
            kwargs.update(request)

        #
        method = kwargs.get("method", "GET")

        #
        default_headers = self.default_headers if self.use_default_headers else {}
        #
        _cookie = ""
        _cookies = {}
        if self.cookie_pool is not None:
            _cookie = self.cookie_pool.get()
            if _cookie:
                if isinstance(_cookie, dict):
                    _cookies = _cookie
                else:
                    default_headers["Cookie"] = _cookie
        #
        _referer = ""
        if self.referer_pool:
            _referer = self.referer_pool.get()
            default_headers["Referer"] = _referer

        #
        headers = kwargs.pop("headers", {})
        if headers:
            default_headers.update(headers)
        kwargs["headers"] = (
            util.format_headers(default_headers)
            if self.format_headers
            else default_headers
        )

        #
        if "verify" not in kwargs:
            kwargs["verify"] = False
        #
        if "proxies" not in kwargs:
            if self.proxy_enable:
                kwargs["proxies"] = self.proxy_pool.get()
                if not kwargs["proxies"]:
                    raise Exception("no valid proxy")
        if "stream" not in kwargs:
            kwargs["stream"] = self.stream
        if "timeout" not in kwargs:
            kwargs["timeout"] = self.timeout

        # user custom session
        _session = kwargs.pop("session", None)

        # use cookies rather than header
        if "cookies" not in kwargs:
            cookies_str = kwargs["headers"].get("Cookie", "")
            cookies_str += ";" + (_session or self.session).headers.get("Cookie", "")
            kwargs["cookies"] = dict(
                [x.split("=", maxsplit=1) for x in cookies_str.split(";") if x.strip()]
            )
            kwargs["cookies"].update(_cookies)
        if ("data" in kwargs or "json" in kwargs) and method == "GET":
            method = "POST"

        #
        for k in ["url", "method"]:
            kwargs.pop(k, None)
        return _session, method, url, kwargs

    def _download_by_httpx(self, method, url, session=None, **kwargs):
        """

        Args:
            method:
            url:
            session:
            **kwargs:

        Returns:

        """
        stream = kwargs.pop("stream")
        proxies: Dict = kwargs.pop("proxies", None)
        verify = kwargs.pop("verify")
        cert = kwargs.pop("cert", None)
        if proxies or verify is not None or cert is not None:
            if proxies is not None:
                proxies = {
                    k if "://" in k else f"{k}://": v for k, v in proxies.items()
                }
            session = self.make_httpx_client(proxies=proxies, verify=verify, cert=cert)
        else:
            if session is None:
                session = self.session if self.use_session else httpx
        if not stream:
            response = session.request(method, url, **kwargs)
        else:
            request = session.build_request(
                method,
                url,
                **{
                    k: kwargs.get(k)
                    for k in [
                        "content",
                        "data",
                        "files",
                        "json",
                        "params",
                        "headers",
                        "cookies",
                        "timeout",
                    ]
                },
            )
            response = session.send(
                request=request,
                auth=kwargs.get("auth"),
                follow_redirects=kwargs.get("follow_redirects"),
                stream=True,
            )
        return response

    @util.retry_decorator(Exception)
    def _download(self, request, **kwargs) -> httpx.Response:
        """

        Args:
            request:
            **kwargs:

        Returns:

        """
        #
        _session, method, url, kwargs = self.prepare_request(request, **kwargs)
        #
        _start = time.time()
        response = self._download_by_httpx(method, url, _session, **kwargs)
        _end = time.time()

        # add meta
        response.meta = {
            "proxies": kwargs.get("proxies", None),
            "headers": kwargs["headers"].copy(),
            "cookies": kwargs["cookies"].copy(),
            "time": {"start": _start, "end": _end, "use": _end - _start,},
        }
        if not kwargs["stream"]:
            response.close()
        return response

    def download(self, request, **kwargs) -> httpx.Response:
        response = None
        exception = None
        is_converted = False
        for i in range(2):
            try:
                response = self._download(request, **kwargs)
                if response is not None:
                    if not response and self.show_fail_log:
                        logger.error(
                            "download failed: {} {}".format(
                                response.status_code, response.url
                            )
                        )
                break
            except Exception as e:
                if not is_converted:
                    exception = e
                    request = self.convert_http_protocol(request)
                    is_converted = True
                else:
                    if self.show_error_log:
                        logger.exception(exception, exc_info=exception)
                    else:
                        logger.error("download exception: {}".format(exception))
        if response is not None:
            response.graper_exception = exception
        return response


if __name__ == "__main__":
    pass
    # 用法示例
    downloader = Downloader(show_error_log=True, proxy_enable=False)
    resp = downloader.download(
        "http://httpbin.org/anything",
        proxies={"http": "http://10.10.91.254:8364"},
        headers={"Accept-Encoding": ""},
        stream=True,
    )
    print(type(resp))
    print(resp)
    print(resp.url)
    resp.read()
    print(resp.text)
    print(resp.headers)
    print(resp.meta)
    resp.close()

    # resp = downloader.download("ftp://127.0.0.1:2121/a.txt")
    # print(resp)
    # print(resp.content.decode("gbk"))

    # print(downloader.convert_http_protocol("https://www.baidu.com"))
