from __future__ import print_function
import libvirt
import sched
import time
import logging
from os import environ
from prometheus_client import start_http_server, Gauge
from xml.etree import ElementTree

logging.basicConfig(level=logging.INFO)

SCRAPE_INTERVAL = int(environ.get("SCRAPE_INTERVAL"))
LIBVIRT_URI = environ.get("LIBVIRT_URI")


def connect_to_uri(uri):
    conn = libvirt.open(uri)

    if conn == None:
        logging.error('Failed to open connection to ' + uri)
    else:
        logging.info('Successfully connected to ' + uri)
    return conn


def get_domains(conn):
    domains = []

    for id in conn.listDomainsID():
        dom = conn.lookupByID(id)

        if dom == None:
            logging.error('Failed to find the domain ' + dom.name())
        else:
            domains.append(dom)

    if len(domains) == 0:
        logging.info('No running domains in URI')
        return None
    else:
        return domains


def get_metrics_collections(metric_names, labels, stats):
    dimensions = []
    metrics_collection = {}

    for mn in metric_names:
        if type(stats) is list:
            dimensions = [[stats[0][mn], labels]]
        elif type(stats) is dict:
            dimensions = [[stats[mn], labels]]
        metrics_collection[mn] = dimensions

    return metrics_collection


def get_metrics_multidim_collections(dom, metric_names, device):

    tree = ElementTree.fromstring(dom.XMLDesc())
    targets = []

    for target in tree.findall("devices/" + device + "/target"): # !
        targets.append(target.get("dev"))

    metrics_collection = {}

    for mn in metric_names:
        dimensions = []
        for target in targets:
            labels = {'domain': dom.name()}
            labels['target_device'] = target
            if device == "interface":
                stats = dom.interfaceStats(target) # !
            elif device == "disk":
                stats= dom.blockStats(target)
            stats = dict(zip(metric_names, stats))
            dimension = [stats[mn], labels]
            dimensions.append(dimension)
            labels = None
        metrics_collection[mn] = dimensions

    return metrics_collection


def add_metrics(dom, header_mn, g_dict):

    labels = {'domain':dom.name()}

    if header_mn == "libvirt_cpu_stats_":

        stats = dom.getCPUStats(True)
        metric_names = stats[0].keys()
        metrics_collection = get_metrics_collections(metric_names, labels, stats)
        unit = "_nanosecs"

    elif header_mn == "libvirt_mem_stats_":
        stats = dom.memoryStats()
        metric_names = stats.keys()
        metrics_collection = get_metrics_collections(metric_names, labels, stats)
        unit = ""

    elif header_mn == "libvirt_block_stats_":

        metric_names = \
        ['read_requests_issued',
        'read_bytes' ,
        'write_requests_issued',
        'write_bytes',
        'errors_number']

        metrics_collection = get_metrics_multidim_collections(dom, metric_names, device="disk")
        unit = ""

    elif header_mn == "libvirt_interface_":

        metric_names = \
        ['read_bytes',
        'read_packets',
        'read_errors',
        'read_drops',
        'write_bytes',
        'write_packets',
        'write_errors',
        'write_drops']

        metrics_collection = get_metrics_multidim_collections(dom, metric_names, device="interface")
        unit = ""

    elif header_mn == "libvirt_domain_":
        metrics_collection = {}
        metrics_dict_list = [
            {'name': 'active', 'func': dom.isActive}, {'name': 'max_memory', 'func': dom.maxMemory},
            {'name': 'max_cpus', 'func': dom.maxVcpus}]
        for metrics_dict in metrics_dict_list:
            dimensions = [[metrics_dict.get('func')(), labels]]
            metrics_collection[metrics_dict.get('name')] = dimensions
        unit = ""

    for mn in metrics_collection:
        metric_name = header_mn + mn + unit
        dimensions = metrics_collection[mn]

        if metric_name not in g_dict.keys():

            metric_help = 'help'
            labels_names = metrics_collection[mn][0][1].keys()

            g_dict[metric_name] = Gauge(metric_name, metric_help, labels_names)

            for dimension in dimensions:
                dimension_metric_value = dimension[0]
                dimension_label_values = dimension[1].values()
                g_dict[metric_name].labels(*dimension_label_values).set(dimension_metric_value)
        else:
            for dimension in dimensions:
                dimension_metric_value = dimension[0]
                dimension_label_values = dimension[1].values()
                g_dict[metric_name].labels(*dimension_label_values).set(dimension_metric_value)
    return g_dict


def job(uri, g_dict, scheduler):
    logging.info('JOB Begun')
    conn = connect_to_uri(uri)
    domains = get_domains(conn)
    while domains is None:
        domains = get_domains(conn)
        time.sleep(SCRAPE_INTERVAL)

    for dom in domains:

        logging.debug(dom.name())

        headers_mn = ["libvirt_cpu_stats_", "libvirt_mem_stats_", \
                      "libvirt_block_stats_", "libvirt_interface_", "libvirt_domain_"]

        for header_mn in headers_mn:
            g_dict = add_metrics(dom, header_mn, g_dict)

    conn.close()
    logging.info('JOB FINISHED')
    scheduler.enter(SCRAPE_INTERVAL, 1, job, (uri, g_dict, scheduler))


def main():

    start_http_server(9177)

    g_dict = {}

    scheduler = sched.scheduler(time.time, time.sleep)
    logging.info('LIBVERT PROMETHEUS EXPORTER STARTED')
    scheduler.enter(0, 1, job, (LIBVIRT_URI, g_dict, scheduler))
    scheduler.run()

if __name__ == '__main__':
    main()
