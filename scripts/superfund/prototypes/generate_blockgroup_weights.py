import pandas as pd

# 1. Load the data
df = pd.read_csv('./pipeline/test_data/downloads/blocks_56.csv', dtype=str)
df['population'] = df['population'].astype(float)

# 2. Create the standard Census IDs
# bg_id = state(2) + county(3) + tract(6) + first digit of block(1) = 12 digits
df['bg_id'] = df['state'] + df['county'] + df['tract'] + df['block'].str[0]

# block_id = state(2) + county(3) + tract(6) + block(4) = 15 digits
df['block_id'] = df['state'] + df['county'] + df['tract'] + df['block']

# 3. Calculate Block Group Totals
bg_totals = df.groupby('bg_id')['population'].transform('sum')

# 4. Calculate popwgt (Handling division by zero for unpopulated areas)
df['popwgt'] = df['population'] / bg_totals
df['popwgt'] = df['popwgt'].fillna(0)

# Ensure population is integer for output (fill missing with 0 then cast)
df['population'] = df['population'].fillna(0).astype(int)

# 5. Save the result (omit the NAME column)
out_path = './pipeline/test_data/downloads/block_population_with_weights_56.csv'
# Build output columns: keep original order, omit 'NAME', and ensure 'population' appears
# immediately before 'popwgt' for human readability.
all_cols = [c for c in df.columns if c != 'NAME']
# Remove population and popwgt if present, we'll append them in desired order
cols_core = [c for c in all_cols if c not in ('population', 'popwgt')]
final_cols = cols_core
if 'population' in df.columns:
    final_cols = final_cols + ['population']
if 'popwgt' in df.columns:
    final_cols = final_cols + ['popwgt']

df[final_cols].to_csv(out_path, index=False)

print("File generated with columns: bg_id, block_id, and popwgt")