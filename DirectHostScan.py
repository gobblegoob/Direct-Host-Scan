'''
    Directly queries websites for their certificates
    The certificate CN and SAN fields are used to identify
    external hosts that are connected to by local hosts.

    Then uses that information to populate designated SNA Hostgroups based on 
    specified search criteria.

    Connections that were not designated and errors will populate an excel 
    report for review.
'''

from SnaAPISession import SnaAPISession
from SnaQuery import SnaQuery
from SnaTagAdd import SnaTagAdd
from OpenSSL import crypto
import ssl, socket
from colorama import Fore, Style
import re
from datetime import datetime
import pandas as pd
import pprint
import json

UNDESIGNATED_HOSTS = [] # A list of hosts and host data that is not assigned to a designated hostgroup

def get_certificate(host, port=443, timeout=2):
    # Need to use an unverified context.
    context = ssl._create_unverified_context()
    conn = socket.setdefaulttimeout(2)
    conn = socket.create_connection((host, port))
    sock = context.wrap_socket(conn, server_hostname=host)
    try:
        der_cert = sock.getpeercert(True)
    finally:
        sock.close()
    return ssl.DER_cert_to_PEM_cert(der_cert)


def get_cert_components(der_cert):
    x509 = crypto.load_certificate(crypto.FILETYPE_PEM, der_cert)

    result = {
        'subject': dict(x509.get_subject().get_components()),
        'signature': x509.get_signature_algorithm(),
        'issuer': x509.get_issuer().get_components(),
        'serialNumber': x509.get_serial_number()
    }

    extensions = (x509.get_extension(i) for i in range(x509.get_extension_count()))

    try:
        extension_data = {e.get_short_name(): str(e) for e in extensions}
        result.update(extension_data)
    except:
        return result
    return result

def peer_ip_list(query_results):
    '''
    Send a flow query result, return a deduplicated list of IP addresses of peers
    :arg: dict
    :return: list
    '''
    peer_ip_list = []
    my_dict = {}
    for key in query_results:
        my_dict[key["peer"]["ipAddress"]] = ''
    
    for i in my_dict:
        peer_ip_list.append(i)
    
    return peer_ip_list


def print_results(url_dict):
    '''
    Neatly prints your URL dictionary of results.
    :arg: dict
    :return:
    '''
    for key in url_dict:
        try:
            print(f'Host: {key}')
            print(url_dict[key]['subject'][b'CN'])
            print(url_dict[key][b'subjectAltName'])
            print(url_dict[key]['serialNumber'])
            print('=' * 80)
        except Exception as e:
            print(f'{Fore.RED}Print Result Error: \nHost: {key}\t{e}')
            continue
    return


def parse_data(url_dict):
    '''
    Parses the data in the url dict and updates hosts.  Updates the tags 
    :arg: dictionary
    :return:
    '''
    tags = SnaTagAdd()
    global UNDESIGNATED_HOSTS

    for hg in search_data:
        tags.get_my_tag_id(hg, api.SNA_TAGS)
        ip_list = []
        for host in url_dict:
            try:
                host_cn = str(url_dict[host]['subject'][b'CN'])
            except KeyError:
                host_cn = f'No CN'
                continue
            try:
                host_san = str(url_dict[host][b'subjectAltName'])
            except KeyError:
                host_san = f'No SAN'
                continue

            if re.search(search_data[hg]['url'], host):
                print(f'Add host {host} to hostgroup {hg}')
                ip_list.append(host)
                continue
            elif re.search(search_data[hg]['search_string'], host_cn):
                print(f'Cert CN Match! Add {host} to hostgroup {hg}')
                ip_list.append(host)
                continue
            elif re.search(search_data[hg]['search_string'], host_san):
                print(f'Cert SAN match! Add {host} to hostgroup {hg}')
                ip_list.append(host)
                continue
            else: 
                UNDESIGNATED_HOSTS.append(
                    {
                        'host': host,
                        'host_cn': host_cn,
                        'host_san': host_san
                    }
                )
                continue
        # update the tags
        print(f'Updating {hg} with {ip_list.__len__()} values...')
        tags.update_tags(ip_list, api)
    return


# Adding new excel_report
def excel_report(data):
    '''
    Print a report of a series of hosts to an excel file for review.
    Filters the DataFrame based on global 'search_data' before creating the report.
    :arg data: dict - A dictionary that can be converted into a Pandas DataFrame.
    :return: boolean - True if the report was created successfully, False otherwise.
    '''
    try:
        today = datetime.today()
        filename = f'{str(today.year)}_{str(today.month)}_{str(today.day)}_DirectHostScan Unknown Report.xlsx'
        df = pd.DataFrame.from_dict(data)

        # --- Step 1: Collect patterns to remove from search_data ---
        patterns_to_remove = []
        for item_value in search_data.values():
            if 'url' in item_value and item_value['url']:
                patterns_to_remove.append(item_value['url'])
            if 'search_string' in item_value and item_value['search_string']:
                patterns_to_remove.append(item_value['search_string'])

        # Remove duplicates and ensure no empty strings are used as patterns
        patterns_to_remove = list(set(patterns_to_remove))

        # --- Step 2: Filter the DataFrame if patterns exist ---
        if patterns_to_remove:
            # Escape special regex characters in the patterns to prevent errors
            # and combine them into a single regex pattern using '|' (OR)
            escaped_patterns = [re.escape(p) for p in patterns_to_remove]
            regex_pattern = '|'.join(escaped_patterns)

            # Create boolean masks for rows that contain any of the patterns
            # .astype(str) ensures all column entries are strings for .str.contains()
            # case=False makes the search case-insensitive
            # na=False treats NaN/None values as not containing the pattern
            mask_cn = df['host_cn'].astype(str).str.contains(regex_pattern, case=False, na=False)
            mask_san = df['host_san'].astype(str).str.contains(regex_pattern, case=False, na=False)

            # Combine masks: a row should be removed if it matches in EITHER host_cn OR host_san
            # Then, select rows where this combined mask is FALSE (i.e., rows to keep)
            df = df[~(mask_cn | mask_san)]

        # --- Step 3: Create the Excel report from the cleaned DataFrame ---
        df.to_excel(filename, index=False, freeze_panes=(1,0))
        return True
    except Exception as e:
        print(f'Error creating log file. {e}')
        return False

if __name__ == '__main__':
    # Hostgroups and search strings. Add as many as necessary.
    '''
    search data format
    {
        'NAME OF HOSTGROUP TO UPDATE': {
            'url': 'YOUR URL CAN MATCH CN OR SAN',
            'search_string': 'YOUR REGEX SEARCH STRING CAN MATCH CN OR SAN'
        }
    }
    
    '''
    ### ADD YOUR HOST GROUPS AND SEARCH DATA HERE ####
    
    search_data = {
        'MY HOST GROUP': {
            'url': 'url.com',
            'search_string': 'search string'
        }
    }
    
    # initialize classes
    api = SnaAPISession()
    query = SnaQuery()
    
    try:
        api.sna_session_init('sna.json')
    except Exception as e:
        print(e)

    # I need to create a custom query for this - 
    query.set_sna_json('sna.json')
    query.FLOW_QUERY_FILE = 'sna-flowquery.json'
    query.QUERY_NAME = 'Direct Scan Flow Query'
    query.QUERY_TIME = 500 # Query time in minutes
    query.init_query(api.SNA_TAGS)
    

    query_results = query.run_flow_query(api)
    peer_list = peer_ip_list(query_results)

    url_dict = {}
  

    for i in peer_list:
        try:
            print(f'{Fore.CYAN}Looking up URL: {Fore.LIGHTGREEN_EX}{i}{Style.RESET_ALL}')
            certificate = get_certificate(i)
        except socket.gaierror as e:
            print(f'{Fore.RED}Could not retrieve {i}; Error\n{e}{Style.RESET_ALL}')

        except socket.timeout as e:
            print(f'{Fore.LIGHTYELLOW_EX}{i} has timed out.{Style.RESET_ALL}')
            pass
        except Exception as e: 
            print(f'{Fore.LIGHTRED_EX}Host {i}{e}{Style.RESET_ALL}')  
            pass
        
        try:
            if certificate is not None:
                url_dict[i] = get_cert_components(certificate)
                certificate = None
        except NameError:
            pass
    

    parse_data(url_dict)
    #print_results(url_dict)
    print('=-' * 80)
    
    excel_report(UNDESIGNATED_HOSTS)
    print('=-' * 80)
    quit()