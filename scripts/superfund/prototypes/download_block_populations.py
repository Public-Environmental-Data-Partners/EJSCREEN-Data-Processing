import requests
import csv
import time

# -----------------------
# Set state FIPS here
# -----------------------
state_fips = "56"   # Wyoming

BASE = "https://api.census.gov/data/2020/dec/pl"

def get_counties(state):
    url = f"{BASE}?get=NAME&for=county:*&in=state:{state}"
    r = requests.get(url)
    r.raise_for_status()
    data = r.json()
    return [row[2] for row in data[1:]]  # county FIPS codes


def get_blocks_for_county(state, county):
    url = (
        f"{BASE}?get=P1_001N,NAME"
        f"&for=block:*"
        f"&in=state:{state}"
        f"&in=county:{county}"
        f"&in=tract:*"
    )
    r = requests.get(url)
    r.raise_for_status()
    return r.json()


def download_state_blocks(state):
    counties = get_counties(state)

    with open(f"./pipeline/test_data/downloads/blocks_{state}.csv", "w", newline="") as f:
        writer = csv.writer(f)

        header_written = False
        p_index = None

        for county in counties:
            print(f"Downloading county {county}...")
            data = get_blocks_for_county(state, county)

            if not header_written:
                # Adjust header: if P1_001N exists, remove it from its position and append
                header = list(data[0])
                try:
                    p_index = header.index('P1_001N')
                except ValueError:
                    p_index = None

                if p_index is not None:
                    # remove the original population column and append with desired label
                    header.pop(p_index)
                    header.append('population')

                writer.writerow(header)
                header_written = True

            # For each data row, move the population value (if present) to the end to match header
            if p_index is not None:
                for row in data[1:]:
                    # defensive: ensure row is a list
                    row = list(row)
                    # pop the population value from its original position and append it
                    try:
                        val = row.pop(p_index)
                    except IndexError:
                        val = '0'
                    # Normalize missing population values to '0'
                    if val is None:
                        val = '0'
                    else:
                        vstr = str(val).strip()
                        if vstr == '' or vstr.lower() == 'nan':
                            val = '0'
                        else:
                            val = vstr
                    row.append(val)
                    writer.writerow(row)
            else:
                writer.writerows(data[1:])

            time.sleep(0.5)  # be polite to API


if __name__ == "__main__":
    download_state_blocks(state_fips)
