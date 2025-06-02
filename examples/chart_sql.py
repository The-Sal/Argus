import sqlite3
import pandas as pd
import matplotlib.pyplot as plt

# Connect to SQLite database
conn = sqlite3.connect('/Users/Salman/Projects/Imperium/Argus/building/ADX_data')
# Create a cursor object
cursor = conn.cursor()
# Execute a SQL query to fetch data
# noinspection all
query_fadx = 'select * from ticker_data  where ticker = "ADX:FADX15" order by timestamp'
# noinspection all
query_chadx = 'select * from ticker_data  where ticker = "ADX:CHADX15" order by timestamp'

cursor.execute(query_fadx)
rows_fadx = cursor.fetchall()
cursor.execute(query_chadx)
rows_chadx = cursor.fetchall()
# Convert the fetched data into a pandas DataFrame
df_fadx = pd.DataFrame(rows_fadx, columns=[column[0] for column in cursor.description])
df_chadx = pd.DataFrame(rows_chadx, columns=[column[0] for column in cursor.description])
# Close the cursor and connection
cursor.close()
conn.close()

# convert timestamps which are epoch to datetime
df_fadx['timestamp'] = pd.to_datetime(df_fadx['timestamp'], unit='s')
df_chadx['timestamp'] = pd.to_datetime(df_chadx['timestamp'], unit='s')
# Set the timestamp as the index
df_fadx.set_index('timestamp', inplace=True)
df_chadx.set_index('timestamp', inplace=True)

# remove all values where price = 0
df_fadx = df_fadx[df_fadx['price'] != 0]
df_chadx = df_chadx[df_chadx['price'] != 0]

# Plot the data
plt.figure(figsize=(12, 6))
plt.plot(df_fadx.index, df_fadx['price'] / df_fadx['price'].iloc[0], label='FADX15', color='blue')
plt.plot(df_chadx.index, df_chadx['price'] / df_chadx['price'].iloc[0], label='CHADX15', color='orange')
plt.show()

# save as CSVs
df_fadx.to_csv('/Users/Salman/Projects/Imperium/Argus/building/fadx.csv')
df_chadx.to_csv('/Users/Salman/Projects/Imperium/Argus/building/chadx.csv')
