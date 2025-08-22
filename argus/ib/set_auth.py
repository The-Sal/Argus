import os
import time
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options


load_dotenv()
def get_auth():
    url = 'https://www.interactivebrokers.co.uk/portal/?action=ACCT_MGMT_MAIN&loginType=1&clt=0&locale=en_US&RL=1#/dashboard'
    ops = Options()
    # ops.add_argument('--headless')
    driver = webdriver.Chrome(options=ops)
    driver.get(url)

    live_paper_toggle_css = 'label'

    try:
        # wait for site to load
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '#btn_accept_cookies'))
        )
    except Exception as e:
        e.__str__()
        print("Warning: Site may not have loaded properly. Continuing anyway...")
    for _ in range(5):
        try:
            # accept cookies
            accept_cookies_button = driver.find_element(By.CSS_SELECTOR, '#btn_accept_cookies')
            accept_cookies_button.click()
            break
        except Exception as e:
            e.__str__()
            time.sleep(0.1)

    if os.environ.get('PAPER_ACCOUNT', '0') == '1':
        try:
            # wait for live/paper toggle to be present
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, live_paper_toggle_css))
            )
            # click the live/paper toggle
            live_paper_toggle = driver.find_element(By.CSS_SELECTOR, live_paper_toggle_css)
            live_paper_toggle.click()
        except Exception as e:
            e.__str__()
            print("Warning: Live/Paper toggle not found. Continuing with default account type...")


    # get the active element
    time.sleep(1)
    username_element = driver.find_element(By.CSS_SELECTOR, '#xyz-field-username')
    uname = os.environ['USERNAME']
    password = os.environ['PASSWORD']
    username_element.send_keys(uname)
    pass_element = driver.find_element(By.CSS_SELECTOR, '#xyz-field-password')
    pass_element.send_keys(password)
    time.sleep(1)
    # press enter
    pass_element.send_keys(Keys.RETURN)

    destination_url = 'https://www.interactivebrokers.co.uk/portal/?loginType=1&action=ACCT_MGMT_MAIN&clt=0#/dashboard'
    destination_url_2 = 'https://www.interactivebrokers.co.uk/portal/?loginType=2&action=ACCT_MGMT_MAIN&clt=0#/dashboard'
    takes = 0
    MAX_TAKES = 60
    while (driver.current_url != destination_url) and (driver.current_url != destination_url_2):
        time.sleep(1)
        print(f"Waiting for login to complete... {takes}/{MAX_TAKES}")
        takes += 1
        if takes > MAX_TAKES:
            print("Login timed out. Exiting...")
            driver.quit()
            raise Exception("Login timed out. Exiting...")



    print("Login complete. Getting cookies...")
    time.sleep(1)
    cookies = driver.get_cookies()
    print("Cookies obtained. Closing browser...")
    driver.quit()
    return cookies

def update_cookies(write_env=True):
    print("Getting auth...")
    cookies = get_auth()
    cookie_env = ' '.join([f"{cookie['name']}={cookie['value']};" for cookie in cookies])
    print('Injecting cookies into environment variable')
    os.environ['IB_COOKIE'] = cookie_env
    print("Cookies injected into environment variable.")
    if write_env:
        print("Writing cookies to .env file...")
        try:
            with open('.env', 'r') as f:
                lines = f.readlines()
        except FileNotFoundError:
            print(".env file not found. Creating a new one.")
            lines = []

        with open('.env', 'w') as f:
            for line in lines:
                if line.startswith('IB_COOKIE'):
                    f.write(f'IB_COOKIE=\'{cookie_env}\' \n')
                else:
                    f.write(line)
    else:
        print("Not writing to .env file.")


def inject_cookies_into_browser():
    chrome = webdriver.Chrome()
    chrome.get('https://www.interactivebrokers.co.uk/portal')
    cookie_env = os.environ['IB_COOKIE']
    cookies = cookie_env.split(';')
    for i in range(int(len(cookies))):
        name = cookies[i].split('=')[0]
        try:
            value = cookies[i].split('=')[1]
            print(f"Injecting cookie {name}={value}")
        except IndexError:
            print(f"Cookie {name} is empty. Skipping...")
            continue
        if len(value) < 3:
            print(f"Cookie {name} is empty. Skipping...")
            continue

        chrome.add_cookie({'name': name.strip(), 'value': value.strip()})

    chrome.get('https://www.interactivebrokers.co.uk/portal')




    input('Press Enter to continue...')


if __name__ == '__main__':
    update_cookies()